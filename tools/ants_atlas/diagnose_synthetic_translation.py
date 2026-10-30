"""Read-only diagnosis of ONE synthetic geometry run; stdout only, no ANTs calls."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args()
    import numpy as np
    import SimpleITK as sitk
    root=args.run.resolve(strict=True)
    report={'run':str(root),'mode':'READ_ONLY_SYNTHETIC_DIAGNOSIS','evidence_sha256':{}}
    def pin(path):
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1048576),b''): h.update(block)
        report['evidence_sha256'][path.name]=h.hexdigest()
    def read(name):
        path=root/name; pin(path); return sitk.ReadImage(str(path))
    def geometry(im):
        return dict(size=im.GetSize(),spacing=im.GetSpacing(),origin=im.GetOrigin(),direction=im.GetDirection(),components=im.GetNumberOfComponentsPerPixel())
    def coordinates(im):
        z,y,x=np.indices(tuple(reversed(im.GetSize())))
        xyz=np.stack((x,y,z),-1)
        B=np.asarray(im.GetDirection()).reshape(3,3)@np.diag(im.GetSpacing())
        return np.asarray(im.GetOrigin())+np.einsum('ij,...j->...i',B,xyz), (x,y,z), B
    fixed=read('fixed.nii.gz'); moving=read('moving.nii.gz')
    p,(x,y,z),B=coordinates(fixed); mp,_,_=coordinates(moving)
    mask=(x>=5)&(x<27)&(y>=5)&(y<27)&(z>=5)&(z<19)
    report['geometry']={'fixed':geometry(fixed),'moving':geometry(moving)}
    translation=np.array([.7,-.5,.3]); expected=p+translation
    center=p.reshape(-1,3).mean(axis=0)
    def phantom(q):
        q=(q-center)/np.array([8.,9.,7.])
        return np.exp(-np.sum(q*q,axis=-1))+.4*np.exp(-np.sum((q-np.array([.7,-.5,.3]))**2*4,axis=-1))
    report['phantom_formula']={'fixed_max_abs_error':float(abs(sitk.GetArrayFromImage(fixed)-phantom(p)).max()),
        'moving_max_abs_error':float(abs(sitk.GetArrayFromImage(moving)-phantom(mp-translation)).max()),
        'analytic_pull_intensity_error':float(abs(phantom(expected-translation)-phantom(p)).max()),
        'expected_pull':'moving sampled at fixed physical point + (0.7,-0.5,0.3) mm',
        'origin_shift_is_sampling_grid_not_an_extra_transform':True}
    matrix_path=root/'syn_0GenericAffine.mat'; pin(matrix_path)
    tx=sitk.ReadTransform(str(matrix_path))
    if tx.GetName()=='CompositeTransform': tx=tx.GetNthTransform(0)
    R=np.array(tx.GetMatrix()).reshape(3,3); c=np.array(tx.GetCenter()); t=np.array(tx.GetTranslation())
    def A(q): return np.einsum('ij,...j->...i',R,q-c)+c+t
    report['affine']={'matrix':R.tolist(),'center':c.tolist(),'translation':t.tolist(),
                      'offset':(c+t-R@c).tolist(),'determinant':float(np.linalg.det(R)),
                      'translation_minus_known':(t-translation).tolist()}
    def stats(values, region=mask):
        v=values[region]; return dict(median=float(np.median(v)),p95=float(np.percentile(v,95)),max=float(v.max()))
    def error(label,q,target=expected):
        e=np.linalg.norm(q-target,axis=-1); location=np.unravel_index(np.where(mask,e,-np.inf).argmax(),e.shape)
        report[label]={'interior_mm':stats(e),'boundary_mm':stats(e,~mask),
            'max_index_zyx':list(map(int,location)),'max_index_xyz':list(map(int,location[::-1])),
            'fixed_physical_lps':p[location].tolist(),'actual_mapping_lps':q[location].tolist(),'expected_mapping_lps':target[location].tolist()}
    affine=A(p); error('affine_only',affine)
    warp=read('syn_1Warp.nii.gz'); inverse=read('syn_1InverseWarp.nii.gz')
    for name,field in [('Warp',warp),('InverseWarp',inverse)]:
        u=sitk.GetArrayFromImage(field); field_points,_,basis=coordinates(field)
        g=np.stack([np.stack(np.gradient(u[...,i],axis=(2,1,0)),axis=-1) for i in range(3)],axis=-2)@np.linalg.inv(basis)
        det=np.linalg.det(g+np.eye(3))
        report[name]={'geometry':geometry(field),'amplitude_mm':stats(np.linalg.norm(u,axis=-1)),
            'amplitude_all_mm':stats(np.linalg.norm(u,axis=-1),np.ones(u.shape[:-1],bool)),
            'jacobian':{'min':float(det.min()),'median':float(np.median(det)),'max':float(det.max()),'nonpositive_count':int((det<=0).sum())}}
    # Direct independent physical-map evaluation, no image registration or resampling command.
    warp_tx=sitk.DisplacementFieldTransform(sitk.Cast(warp,sitk.sitkVectorFloat64))
    def W(q):
        return np.array([warp_tx.TransformPoint(tuple(v)) for v in q.reshape(-1,3)]).reshape(q.shape)
    aw=A(W(p)); wa=W(A(p))
    error('full_candidate_A_after_W',aw); error('full_candidate_W_after_A',wa)
    report['nonlinear_mapping_change_mm']=stats(np.linalg.norm(aw-affine,axis=-1))
    # Actual saved ANTs coordinate probes discriminate composition and clipping effects.
    if all((root/f'syn_forward_{i}.nii.gz').exists() for i in range(3)):
        saved=np.stack([sitk.GetArrayFromImage(read(f'syn_forward_{i}.nii.gz')) for i in range(3)],axis=-1)
        error('actual_ants_coordinate_probes',saved)
        report['probe_vs_A_after_W_mm']=stats(np.linalg.norm(saved-aw,axis=-1))
        report['probe_vs_W_after_A_mm']=stats(np.linalg.norm(saved-wa,axis=-1))
        for label,q in [('A_after_W',aw),('W_after_A',wa)]:
            index=np.einsum('ij,...j->...i',np.linalg.inv(B),q-np.asarray(fixed.GetOrigin()))
            valid=np.all((index>=0)&(index<=np.array(fixed.GetSize())-1),axis=-1)
            report[label+'_outside_coordinate_probe_domain_interior']=int((mask&~valid).sum())
    else: report['actual_ants_coordinate_probes']='MISSING; no reconstruction or rerun performed'
    logs=[]
    for path in sorted(root.glob('command_*.log')):
        lines=path.read_text(encoding='utf-8',errors='replace').splitlines()
        if not lines or 'antsRegistration' not in lines[0]: continue
        pin(path)
        hits=[{'line':i+1,'text':line} for i,line in enumerate(lines)
              if re.search(r'warn|error|exception|converg|stage|DIAGNOSTIC|elapsed',line,re.I)]
        logs.append({'file':path.name,'command':lines[0],'line_count':len(lines),'diagnostic_lines':hits,
                     'tail':lines[-25:]})
    report['registration_logs']=logs
    if (root/'summary.json').exists():
        pin(root/'summary.json'); summary=json.loads((root/'summary.json').read_text(encoding='utf-8-sig'))
        report['recorded_status']=summary.get('status'); report['recorded_failure']=summary.get('failure')
        report['registration_command_receipts']=[v for v in summary.get('commands',[]) if 'antsRegistration' in v.get('argv',[''])[0] and '--version' not in v.get('argv',[])]
    report['interpretation_limits']=['Positive Jacobian means locally orientation preserving, not accurate translation recovery.',
        'Compare affine-only versus full and saved probes before attributing the failure; no causal conclusion from magnitude alone.',
        'No parameters/tolerances changed; no automatic retry; only listed synthetic files read.']
    print(json.dumps(report,indent=2,allow_nan=False))


if __name__=='__main__': main()
