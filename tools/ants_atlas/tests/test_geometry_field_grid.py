"""Small grid metadata regression, no ANTs or image file reads."""
import importlib.util
from pathlib import Path
import unittest
import SimpleITK as sitk
spec=importlib.util.spec_from_file_location('geometry',Path(__file__).parents[1]/'validate_windows_geometry.py')
g=importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
class FieldGridTests(unittest.TestCase):
    def test_fixed_storage_domain_despite_different_moving_origin(self):
        fixed=sitk.Image([32,32,24],sitk.sitkFloat32)
        fixed.SetOrigin((-18,7,-11)); fixed.SetSpacing((1.2,1.5,1.8))
        moving=sitk.Image(fixed); moving.SetOrigin((-17.8,7,-11))
        field=sitk.Image(fixed.GetSize(),sitk.sitkVectorFloat64,3); field.CopyInformation(fixed)
        for name in ('syn_1Warp.nii.gz','syn_1InverseWarp.nii.gz'):
            g.check_syn_field_grid(field,fixed,name)
        field.CopyInformation(moving)
        with self.assertRaisesRegex(RuntimeError,'syn_1InverseWarp.*actual=.*expected fixed'):
            g.check_syn_field_grid(field,fixed,'syn_1InverseWarp.nii.gz')
        for attr, value in [('Spacing',(1.,1.5,1.8)),('Direction',(-1.,0,0,0,1.,0,0,0,1.))]:
            field.CopyInformation(fixed); getattr(field,'Set'+attr)(value)
            with self.assertRaises(RuntimeError): g.check_syn_field_grid(field,fixed,attr)
        with self.assertRaises(RuntimeError): g.check_syn_field_grid(fixed,fixed,'scalar')
