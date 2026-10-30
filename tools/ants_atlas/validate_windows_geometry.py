"""Bounded real-Windows-ANTs synthetic deployment validation. Never reads patient data.
Run only explicitly on deployment host. No retry and no tolerance relaxation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def physical_gradient(u, direction, spacing):
    import numpy as np
    return np.stack([np.stack(np.gradient(u[...,i],axis=(2,1,0)),axis=-1) for i in range(3)],axis=-2) @ np.linalg.inv(direction @ np.diag(spacing))


def check_shear_jacobian(u, xyz, direction, spacing, interior, amplitude=.35, length=8.):
    import numpy as np
    basis=direction @ np.diag(spacing)
    analytic=np.zeros(xyz.shape[:-1]+(3,3))
    analytic[...,0,1]=amplitude/length*np.cos(xyz[...,1]/length)
    analytic_error=np.abs(np.linalg.det(analytic+np.eye(3))-1)
    require(analytic_error.max()<1e-6, 'Analytic shear Jacobian differs from one')
    numeric=physical_gradient(u,direction,spacing)
    # Unit-index central error <= max third derivative / 6.
    # First-order boundary error <= max second derivative / 2.
    bounds=np.zeros(xyz.shape)
    for j,axis in enumerate((2,1,0)):
        h=abs(basis[1,j])
        bounds[...,j]=abs(amplitude)*h**3/(6*length**3)
        edge=[slice(None)]*3
        for endpoint in (0,-1):
            edge[axis]=endpoint
            bounds[tuple(edge)+(j,)]=abs(amplitude)*h**2/(2*length**2)
    bound=bounds @ np.abs(np.linalg.inv(basis))+1e-12
    delta=np.abs(numeric-analytic)
    require(np.isfinite(numeric).all() and np.all(delta[...,0,:]<=bound) and delta[...,1:,:].max()<=1e-12,
            'Finite-difference shear derivative exceeds truncation bound')
    determinant=np.linalg.det(numeric+np.eye(3)); error=abs(determinant-1)
    require(determinant.min()>0,'Finite-difference shear folding')
    return dict(analytic_det_error=float(analytic_error.max()), finite_det_max=float(error.max()),
                max_location_zyx=list(map(int,np.unravel_index(error.argmax(),error.shape))),
                interior_det_error=float(error[interior].max()),boundary_det_error=float(error[~interior].max()),
                interior_derivative_error=float(delta[interior].max()),boundary_derivative_error=float(delta[~interior].max()),
                interior_derivative_bound=float(bound[interior].max()),boundary_derivative_bound=float(bound[~interior].max()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ants-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(os.name == 'nt', 'Windows deployment only')
    import numpy as np
    import SimpleITK as sitk
    import psutil
    root = Path(__file__).resolve().parents[2]
    require(args.output.is_absolute(), 'Output must be absolute')
    out = args.output.resolve()
    ants = args.ants_root.resolve(strict=True)
    require(out.is_absolute() and not out.exists(), 'Unique new absolute output directory required')
    for protected in (root, ants):
        require(not out.is_relative_to(protected) and not protected.is_relative_to(out), 'Output overlaps protected source/tools')
    out.mkdir(parents=True, exist_ok=False)
    state = dict(status='RUNNING', experiment_class='SYNTHETIC_ENGINEERING_ONLY', commands=[], checks={}, errors={},
                 threads=2, gpu_gate='DISABLED_BY_USER_CPU_ONLY_ANTS', started_utc=datetime.now(timezone.utc).isoformat(),
                 atlas_sha256=digest(root/'tools/ants_atlas/atlas.py'),
                 scheduler_sha256=digest(root/'tools/ants_atlas/scheduler.py'), script_sha256=digest(__file__))
    started = time.monotonic()
    env = os.environ.copy()
    env.update(ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS='2', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', ANTS_RANDOM_SEED='20261028')
    env['PATH'] = os.pathsep.join([str(ants/'bin'), str(ants/'lib'), env.get('PATH','')])
    tools = {name: ants/'bin'/(name+'.exe') for name in
             ('antsApplyTransforms','antsRegistration','AverageImages','AverageAffineTransform','MultiplyImages')}
    def save():
        (out/'summary.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
    def guard():
        cpu = psutil.cpu_percent(interval=.2)
        mem = psutil.virtual_memory()
        state['last_resource'] = dict(cpu_percent=cpu, ram_percent=mem.percent)
        require(cpu < 80 and mem.percent < 80, 'CPU/RAM 80% gate')
        require(time.monotonic()-started < 1200, '20 minute total budget exceeded')
    def command(name, argv, outputs=(), timeout=180):
        guard()
        entry = dict(argv=[str(tools[name]), *map(str,argv)], outputs=list(map(str,outputs)), exit_code=None)
        state['commands'].append(entry); save()
        with (out/('command_%03d.log' % (len(state['commands'])-1))).open('x', encoding='utf-8') as log:
            log.write(json.dumps(entry['argv'])+'\n'); log.flush()
            proc = subprocess.Popen(entry['argv'], stdout=log, stderr=subprocess.STDOUT, env=env, shell=False)
            owned = psutil.Process(proc.pid); identity = owned.create_time(); t = time.monotonic()
            try:
                while proc.poll() is None:
                    guard()
                    require(time.monotonic()-t < timeout, 'Command timeout')
                    time.sleep(.2)
            finally:
                if proc.poll() is None:
                    try:
                        if owned.create_time() == identity:
                            for child in owned.children(recursive=True): child.kill()
                            owned.kill()
                    except psutil.NoSuchProcess:
                        pass
                    proc.wait(timeout=10)
                entry['exit_code'] = proc.returncode; save()
        require(proc.returncode == 0, 'ANTs command failed')
        for path in outputs:
            require(Path(path).is_file() and Path(path).stat().st_size > 0, 'Missing/empty output: '+str(path))
        return (out/('command_%03d.log' % (len(state['commands'])-1))).read_text(encoding='utf-8')
    def write(array, name, vector=False, reference=None):
        image = sitk.GetImageFromArray(np.asarray(array, dtype=np.float64 if vector else np.float32), isVector=vector)
        image.CopyInformation(reference or grid)
        path = out/name; sitk.WriteImage(image,str(path)); return path
    def read(path): return sitk.ReadImage(str(path))
    def arr(path): return sitk.GetArrayFromImage(read(path))
    def apply(source, reference, name, transforms, interpolation='Linear', kind='scalar'):
        path=out/name
        command('antsApplyTransforms',['-d',3,'--float',1,'-e',kind,'-i',source,'-r',reference,'-o',path,'-n',interpolation,
                    '--default-value',0,*sum((['-t',str(t)] for t in transforms),[])],[path])
        return path
    def check_error(name, actual, expected, mask, tolerance=.02):
        error=np.linalg.norm(actual-expected,axis=-1)
        interior=float(error[mask].max()); boundary=float(error[~mask].max())
        state['errors'][name]=dict(interior_mm=interior,boundary_mm=boundary,tolerance_mm=tolerance)
        require(np.isfinite(error).all() and interior < tolerance, name+' independent reference mismatch')
    try:
        sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(1)
        state['exe_sha256']={str(path):digest(path) for path in tools.values()}
        state['tool_versions']={}
        text=command('antsRegistration',['--version'],timeout=20)
        state['tool_versions']={'antsRegistration': text.strip(),
                                'other_tools': 'same deployment directory; exact per-executable SHA256 recorded'}
        require('2.6.5' in state['tool_versions']['antsRegistration'], 'Unexpected ANTs version')
        # Physical coordinates, not array indices. Three independent scalar coordinate probes.
        grid=sitk.Image([32,32,24],sitk.sitkFloat32)
        angle=.23; D=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
        grid.SetDirection(tuple(D.ravel())); grid.SetSpacing((1.2,1.5,1.8)); grid.SetOrigin((-18.,7.,-11.))
        z,y,x=np.indices((24,32,32)); indices=np.stack((x,y,z),-1)
        xyz=np.array(grid.GetOrigin())+np.einsum('ij,...j->...i',D,indices*np.array(grid.GetSpacing()))
        mask=(x>=5)&(x<27)&(y>=5)&(y<27)&(z>=5)&(z<19)
        reference=write(np.ones(x.shape),'reference.nii.gz')
        probes=[write(xyz[...,i],f'coordinate_{i}.nii.gz') for i in range(3)]
        def mapping(label, transforms, ref=reference):
            return np.stack([arr(apply(p,ref,f'{label}_{i}.nii.gz',transforms)) for i,p in enumerate(probes)],-1)
        zero=write(np.zeros(x.shape+(3,)),'identity.nii.gz',True)
        check_error('identity',mapping('identity',[zero]),xyz,mask)
        state['checks']['identity_field']=True
        translation=np.array([.7,-.5,.3])
        shift=write(np.broadcast_to(translation,xyz.shape),'translation.nii.gz',True)
        check_error('translation',mapping('translation',[shift]),xyz+translation,mask)
        state['checks']['analytic_translation']=True
        # Analytically invertible physical shear: x'=x+a sin(y/L), inverse changes sign.
        a=.35; L=8.
        displacement=np.zeros_like(xyz); displacement[...,0]=a*np.sin(xyz[...,1]/L)
        warp=write(displacement,'shear.nii.gz',True)
        inverse=write(-displacement,'inverse_shear.nii.gz',True)
        affine=sitk.AffineTransform(3); R=np.array([[1,.04,0],[0,1,.03],[0,0,1.]])
        affine.SetMatrix(tuple(R.ravel())); affine.SetTranslation((.2,.1,-.1))
        mat=out/'affine.mat'; sitk.WriteTransform(affine,str(mat))
        def A(points): return np.einsum('ij,...j->...i',R,points)+np.array(affine.GetTranslation())
        def Ai(points): return np.einsum('ij,...j->...i',np.linalg.inv(R),points-np.array(affine.GetTranslation()))
        def W(points,sign=1):
            result=points.copy(); result[...,0]+=sign*a*np.sin(points[...,1]/L); return result
        check_error('noncommuting_forward',mapping('forward',[warp,mat]),A(W(xyz)),mask)
        check_error('noncommuting_inverse',mapping('inverse',[f'[{mat},1]',inverse]),W(Ai(xyz),-1),mask)
        state['checks']['noncommuting_chain']=True
        jac=sitk.GetArrayFromImage(sitk.DisplacementFieldJacobianDeterminant(read(warp)))
        state['errors']['analytic_jacobian']=check_shear_jacobian(displacement, xyz, D, np.array(grid.GetSpacing()), mask, a, L)
        require(np.isfinite(jac).all() and np.min(jac)>0, 'Nonpositive field Jacobian')
        state['checks']['jacobian']=True
        # Actual production vector averaging/scaling/reorientation and four compositions.
        avg=out/'average_warp.nii.gz'; scaled=out/'negative_warp.nii.gz'; avgmat=out/'average.mat'
        command('AverageImages',[3,avg,0,warp,warp],[avg])
        command('MultiplyImages',[3,avg,-.25,scaled],[scaled])
        command('AverageAffineTransform',[3,avgmat,mat,mat],[avgmat])
        physical=apply(scaled,reference,'physical.nii.gz',[f'[{avgmat},1]'],kind='physical-vector')
        # Numerical reference: resample physical vectors, then inverse pull's inverse linear action.
        transformed=Ai(xyz)
        expected_vector=-.25*(W(transformed)-transformed)
        expected_vector=np.einsum('ij,...j->...i',R,expected_vector)
        check_error('physical_vector',arr(physical),expected_vector,mask,.025)
        expected=xyz.copy()
        for _ in range(4):
            q=Ai(expected); v=-.25*(W(q)-q)
            expected+=np.einsum('ij,...j->...i',R,v)
        expected=Ai(expected)
        check_error('shape_update',mapping('shape',[f'[{avgmat},1]',*[physical]*4]),expected,mask,.04)
        state['checks']['physical_vector_shape_update']=True
        # Actual small SyN; independently check forward mapping against known synthetic translation.
        center=np.mean(xyz.reshape(-1,3),axis=0)
        def phantom(p):
            q=(p-center)/np.array([8.,9.,7.])
            return np.exp(-np.sum(q*q,axis=-1))+.4*np.exp(-np.sum((q-np.array([.7,-.5,.3]))**2*4,axis=-1))
        fixed=write(phantom(xyz),'fixed.nii.gz')
        moving_grid=sitk.Image(grid); moving_grid.SetOrigin(tuple(np.array(grid.GetOrigin())+np.array([.2,0,0])))
        moving_xyz=xyz+np.array([.2,0,0])
        moving=write(phantom(moving_xyz-translation),'moving.nii.gz',reference=moving_grid)
        prefix=out/'syn_'
        command('antsRegistration',['-d',3,'--float',1,'--random-seed',20261028,'-u',0,'-z',1,
            '-r',f'[{fixed},{moving},1]','-t','Rigid[0.1]','-m',f'MI[{fixed},{moving},1,32,Regular,0.25]',
            '-c','[40x20,1e-6,10]','-f','2x1','-s','1x0vox',
            '-t','Affine[0.1]','-m',f'MI[{fixed},{moving},1,32,None,1]',
            '-c','[40x20,1e-6,10]','-f','2x1','-s','1x0vox',
            '-t','SyN[0.1,3,0]','-m',f'CC[{fixed},{moving},1,4]',
            '-c','[30x15,1e-6,10]','-f','2x1','-s','1x0vox','-o',str(prefix)],
            [str(prefix)+'0GenericAffine.mat',str(prefix)+'1Warp.nii.gz',str(prefix)+'1InverseWarp.nii.gz'],timeout=600)
        forward=[str(prefix)+'1Warp.nii.gz',str(prefix)+'0GenericAffine.mat']
        backward=[f'[{prefix}0GenericAffine.mat,1]',str(prefix)+'1InverseWarp.nii.gz']
        for filename,ref in ((forward[0],fixed),(backward[1],moving)):
            field=read(filename); target=read(ref)
            require(field.GetNumberOfComponentsPerPixel()==3 and field.GetSize()==target.GetSize() and
                    np.allclose(field.GetOrigin(),target.GetOrigin()) and np.allclose(field.GetDirection(),target.GetDirection()) and
                    np.allclose(field.GetSpacing(),target.GetSpacing()), 'SyN field reference domain mismatch')
            vector=sitk.GetArrayFromImage(field)
            derivative=np.stack([np.stack(np.gradient(vector[...,i],axis=(2,1,0)),axis=-1) for i in range(3)],axis=-2)
            basis=np.asarray(field.GetDirection()).reshape(3,3)@np.diag(field.GetSpacing())
            determinant=np.linalg.det(derivative@np.linalg.inv(basis)+np.eye(3))
            require(np.isfinite(determinant).all() and np.min(determinant)>0,'SyN folding')
            state['errors'][Path(filename).name+'_jacobian']={'min':float(determinant.min()),'max':float(determinant.max())}
        mapped=mapping('syn_forward',forward)
        check_error('syn_known_translation',mapped,xyz+translation,mask,.25*min(grid.GetSpacing()))
        returned=[]
        for i in range(3):
            first=out/f'syn_forward_{i}.nii.gz'
            returned.append(arr(apply(first,moving,f'roundtrip_{i}.nii.gz',backward)))
        returned=np.stack(returned,-1)
        check_error('roundtrip',returned,moving_xyz,mask,.25*min(grid.GetSpacing()))
        state['interior_roundtrip_error_min_spacing']=state['errors']['roundtrip']['interior_mm']/min(grid.GetSpacing())
        state['boundary_error_reported']=True
        coverage=apply(reference,fixed,'coverage.nii.gz',forward,'NearestNeighbor')
        require(set(np.unique(arr(coverage))) <= {0,1}, 'Coverage not binary')
        # Independent source-domain occupancy from physical mapping, NN half-voxel domain.
        ci=np.einsum('ij,...j->...i',np.linalg.inv(D),mapped-np.array(grid.GetOrigin()))/np.array(grid.GetSpacing())
        expected_coverage=np.all((ci>=-.5)&(ci<np.array(grid.GetSize())-.5),axis=-1)
        require(np.array_equal(arr(coverage)[mask],expected_coverage[mask]), 'NN coverage reference mismatch')
        native=read(out/'roundtrip_0.nii.gz')
        require(native.GetSize()==grid.GetSize() and np.allclose(native.GetOrigin(),moving_grid.GetOrigin()) and
                np.allclose(native.GetSpacing(),grid.GetSpacing()) and np.allclose(native.GetDirection(),grid.GetDirection()),'Native grid mismatch')
        state['checks'].update(native_grid=True,binary_coverage=True,oblique_lps_spacing=True,real_windows_ants=True)
        require(digest(root/'tools/ants_atlas/atlas.py')==state['atlas_sha256'] and
                digest(root/'tools/ants_atlas/scheduler.py')==state['scheduler_sha256'], 'Code changed during validation')
        require(all(digest(p)==h for p,h in state['exe_sha256'].items()), 'Tool changed during validation')
        state['status']='PASS'
        receipt={**state,**state['checks'], 'official_source_sha256':'80441088eda2195b98286dc04db71d2878e41e3ea1f2d8f0db26d84bb6639d87'}
        receipt['output_sha256']={str(p):digest(p) for p in out.iterdir() if p.is_file() and p.name!='summary.json'}
        (out/'deployment_receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    except BaseException as exc:
        state.update(status='FAILED', failure=type(exc).__name__+': '+str(exc))
        raise
    finally:
        state['ended_utc']=datetime.now(timezone.utc).isoformat(); save()


if __name__ == '__main__':
    main()
