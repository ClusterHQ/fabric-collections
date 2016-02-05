
import unittest

from zope.interface.verify import verifyObject, verifyClass

from bookshelf.api_v3.gce import GCE
from bookshelf.api_v3.ec2 import EC2
from bookshelf.api_v3.cloud_instance import (
    ICloudInstanceFactory,
    ICloudInstance
)


class TestGCEInterfaces(unittest.TestCase):

    def test_gce_provides_cloud_instance_factory(self):
        verifyObject(ICloudInstanceFactory, GCE)

    def test_gce_implements_cloud_instance(self):
        verifyClass(ICloudInstance, GCE)


class TestEC2Interfaces(unittest.TestCase):

    def test_gce_provides_cloud_instance_factory(self):
        verifyObject(ICloudInstanceFactory, EC2)

    def test_gce_implements_cloud_instance(self):
        verifyClass(ICloudInstance, EC2)


if __name__ == '__main__':
    unittest.main(verbosity=4, failfast=True)
