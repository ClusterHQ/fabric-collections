
import unittest

from zope.interface.verify import verifyObject, verifyClass

from bookshelf.api_v3.gce import GCE
from bookshelf.api_v3.ec2 import EC2
from bookshelf.api_v3.rackspace import Rackspace
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

    def test_ec2_provides_cloud_instance_factory(self):
        verifyObject(ICloudInstanceFactory, EC2)

    def test_ec2_implements_cloud_instance(self):
        verifyClass(ICloudInstance, EC2)


class TestRackspaceInterfaces(unittest.TestCase):

    def test_rackspace_provides_cloud_instance_factory(self):
        verifyObject(ICloudInstanceFactory, Rackspace)

    def test_rackspaceee_implements_cloud_instance(self):
        verifyClass(ICloudInstance, Rackspace)


if __name__ == '__main__':
    unittest.main(verbosity=4, failfast=True)
