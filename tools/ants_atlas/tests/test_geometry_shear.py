import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
spec=importlib.util.spec_from_file_location('geometry',Path(__file__).parents[1]/'validate_windows_geometry.py')
g=importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
class ShearTests(unittest.TestCase):
    def test_truncation_and_wrong_geometry_rejected(self):
        z,y,x=np.indices((24,32,32)); t=.23
        D=np.array([[np.cos(t),-np.sin(t),0],[np.sin(t),np.cos(t),0],[0,0,1.]])
        spacing=np.array([1.2,1.5,1.8]); xyz=np.array([-18.,7.,-11.])+np.einsum('ij,...j->...i',D,np.stack((x,y,z),-1)*spacing)
        u=np.zeros_like(xyz); u[...,0]=.35*np.sin(xyz[...,1]/8)
        mask=(x>=5)&(x<27)&(y>=5)&(y<27)&(z>=5)&(z<19)
        print(g.check_shear_jacobian(u,xyz,D,spacing,mask))
        for wrong in (np.eye(3),D[:,::-1]):
            with self.assertRaises(RuntimeError): g.check_shear_jacobian(u,xyz,wrong,spacing,mask)
        correct=g.physical_gradient(u,D,spacing)
        with patch.object(g,'physical_gradient',return_value=correct[...,::-1]):
            with self.assertRaises(RuntimeError): g.check_shear_jacobian(u,xyz,D,spacing,mask)
