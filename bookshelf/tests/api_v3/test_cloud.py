import unittest

from subprocess import check_output
from uuid import uuid4
import yaml
import os

from bookshelf.api_v3.cloud_instance import Distribution, ICloudInstance
from bookshelf.api_v3.rackspace import (
    RackspaceInstance, RackspaceConfiguration
)
from bookshelf.api_v3.gce import GCEInstance, GCEConfiguration
from bookshelf.api_v3.ec2 import EC2Instance, EC2Configuration, EC2Credentials
from zope.interface.verify import verifyObject


class CloudInstanceTestMixin(object):
    """
    Mixin test structure for tests that verify a cloud instance is functional.

    Assumes the following members are initialized in setUp():

    self.instance_factory: An ICloudInstanceFactory provider.
    self.config: A valid configuration for the ICloudInstanceFactory provider.
    self.distribution: The distribution that the config launches.
    self.region: A valid region  for the ICloudInstanceFactory provider.
    """

    def _make_instance(self):
        """
        Create an instance using the factory, from member variables that must
        be set by the subclass.
        """
        instance = self.instance_factory.create_from_config(
            self.config, self.distribution, self.region
        )
        self.addCleanup(instance.destroy)
        return instance

    def _restore_from_state(self, state):
        """
        Create an instance object by restoring from state.

        Does not clean up by destroying the instance, because this never
        creates an instance, merely re-connects to one created by something
        else.
        """
        instance = self.instance_factory.create_from_saved_state(
            self.config, state
        )
        return instance

    def _instance_sanity_check(self, instance):
        """
        Verify that the instance implements the ``ICloudInstance`` interface,
        and the information it provides can be used to ssh to the instance.
        """
        verifyObject(ICloudInstance, instance)
        _TEST_STRING = b"Hello12345"
        output = check_output([
            "ssh", "-i", instance.key_filename,
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "StrictHostKeyChecking=no",
            "%s@%s" % (instance.username, instance.ip_address),
            "echo", _TEST_STRING])
        self.assertIn(_TEST_STRING, output)

    def _assert_instances_are_same(self, instance1, instance2,
                                   verify_ips=True):
        """
        Assert that two instance objects probably refer to the same instance.

        :param bool verify_ips: Whether we should verify that the instances
            have the same IP. Can be false in situations where you are
            comparing an instance from before an up/down cycle to one after an
            up/down cycle.
        """
        self.assertEquals(instance1.distro, instance2.distro)
        self.assertEquals(instance1.username, instance2.username)
        self.assertEquals(instance1.image_basename, instance2.image_basename)
        self.assertEquals(instance1.region, instance2.region)
        self.assertEquals(instance1.key_filename, instance2.key_filename)
        if verify_ips:
            self.assertEquals(instance1.ip_address, instance2.ip_address)

    def test_instance_factory_and_instance(self):
        """
        This is one large test that verifies all of the functionality of a
        specific implementation of an instance factory and the instances that
        it creates.

        Done as one large test rather than many little ones because it takes a
        lot of time to setup the initial instance. The bad part about this is
        it might be a bit time consuming to debug and reproduce failures.
        """
        instance = self._make_instance()
        self._instance_sanity_check(instance)

        up_state = instance.get_state()

        restored_instance = self._restore_from_state(up_state)
        self._instance_sanity_check(restored_instance)
        self._assert_instances_are_same(instance, restored_instance)

        instance.down()
        down_state = instance.get_state()

        # Up the instance by reviving the state.
        revived_instance = self._restore_from_state(down_state)
        self._instance_sanity_check(revived_instance)
        self._assert_instances_are_same(
            instance, revived_instance, verify_ips=False)

        unique_id = revived_instance.create_image(
            'testing-image-%s' % str(uuid4()))

        revived_instance.list_images()
        revived_instance.delete_image(unique_id)


class MissingConfigError(Exception):
    """
    Error that is raised to indicate that some required configuration key was
    not specified.
    """
    pass


def _extract_substructure(base, substructure):
    """
    Assuming that substructure is a possibly nested dictionary, return a new
    dictionary with the same keys (and subkeys) as substructure, but extract
    the leaf values from base.

    This is used to extract and verify a configuration from a yaml blob.
    """
    if (type(substructure) is not dict and
            type(base) is not dict):
        return base
    if type(base) is not dict:
        raise MissingConfigError(
            "Found non-dict value {} when expecting a sub-configuration "
            "{}.".format(repr(base), repr(substructure)))
    if type(substructure) is not dict:
        raise MissingConfigError(
            "Found dict value {} when expecting a simple configuration value "
            "{}.".format(repr(base), repr(substructure)))
    try:
        return {key: _extract_substructure(base[key], substructure[key])
                for key in substructure.keys()}
    except KeyError as e:
        raise MissingConfigError(
            "Missing key {} in configuration".format(e.args[0]))


def _load_config_from_yaml():
    """
    Load configuration from a yaml file specified in an environment variable.

    Raises a SkipTest exception if the environment variable is not specified.
    """
    _ENV_VAR = 'ACCEPTANCE_YAML'
    filename = os.environ.get(_ENV_VAR)
    if not filename:
        print (
            'Must set {} to an acceptance.yaml file ('
            'http://doc-dev.clusterhq.com/gettinginvolved/appendix.html#acceptance-testing-configuration'  #noqa
            ') plus additional keys in order to run this test.'.format(
                _ENV_VAR))
        raise unittest.SkipTest()
    with open(filename) as f:
        config = yaml.safe_load(f)
    return config


def _get_yaml_config(substructure, config=None):
    """
    Extract the keys from the config in substructure, which may be a nested
    dictionary.

    Raises a ``unittest.SkipTest`` if the substructure is not found in the
    configuration.

    This can be used to load credentials all at once for testing purposes.
    """
    if config is None:
        config = _load_config_from_yaml()
    try:
        return _extract_substructure(config, substructure)
    except MissingConfigError as e:
        print (
            'Skipping test: could not get configuration: {}\n\n'
            'In order to run this test, add ensure file at $ACCEPTANCE_YAML '
            'has structure like:\n\n{}'.format(
                e.message,
                yaml.dump(substructure, default_flow_style=False)))
        raise unittest.SkipTest()


class RackspaceTests(unittest.TestCase, CloudInstanceTestMixin):
    """
    Tests for rackspace.
    """

    def setUp(self):
        super(RackspaceTests, self).setUp()

        credentials = _get_yaml_config(
            {
                'rackspace': {
                    'keyname': '<Rackspace keypair name>',
                    'username': '<Rackspace username>',
                    'key': '<Rackspace API key>',
                },
                'ssh_keys': {
                    'rackspace': {
                        'private_key_file': '<path-to-private-keypair-file>',
                        'public_key_file': '<path-to-corresponding-public-key>'
                    }
                }
            }
        )

        ssh_keys = credentials["ssh_keys"]["rackspace"]
        self.config = RackspaceConfiguration(
            username='root',
            instance_type='1GB Standard Instance',
            key_pair=credentials["rackspace"]["keyname"],
            public_key_filename=ssh_keys["public_key_file"],
            private_key_filename=ssh_keys["private_key_file"],
            access_key_id=credentials["rackspace"]["username"],
            secret_access_key=credentials["rackspace"]["key"],
            ami='CentOS 7 (PVHVM)',
            description='rackspace-test-instance-description',
            image_basename='rackspace-test-image',
            instance_name='rackspace-test-instance'
        ).serialize()
        self.distribution = Distribution.CENTOS7
        self.region = 'HKG'
        self.instance_factory = RackspaceInstance


class GCETests(unittest.TestCase, CloudInstanceTestMixin):
    """
    Tests for GCE.
    """

    def setUp(self):
        super(GCETests, self).setUp()
        raw_config = _load_config_from_yaml()
        credentials = _get_yaml_config(
            {
                'gce': {
                    'project': '<GCE project>',
                },
                'ssh_keys': {
                    'gce': {
                        'private_key_file': '<path-to-private-key-file>',
                        'public_key_file': '<path-to-corresponding-public-key>'
                    }
                }
            },
            config=raw_config
        )

        keys = credentials["ssh_keys"]["gce"]

        # If specified, attempt to use the service account credentials from
        # `acceptance.yml` rather than the default authentication method.
        service_account_creds = (
            raw_config.get('gce', {}).get('gce_credentials', {}))

        self.config = GCEConfiguration(
            credentials_private_key=(
                service_account_creds.get('private_key', '')),
            credentials_email=service_account_creds.get('client_email', ''),
            public_key_filename=keys["public_key_file"],
            private_key_filename=keys["private_key_file"],
            project=credentials["gce"]["project"],
            machine_type="n1-standard-1",
            image_basename="gce-test-image",
            username="gce-username-xyz",
            description="gce-test-description",
            instance_name="gce-test-instance",
            base_image_prefix='ubuntu-1404',
            base_image_project='ubuntu-os-cloud'
        ).serialize()
        self.distribution = Distribution.UBUNTU1404
        self.region = 'us-central1-f'
        self.instance_factory = GCEInstance


class EC2Tests(unittest.TestCase, CloudInstanceTestMixin):
    """
    Tests for EC2.
    """

    def setUp(self):
        super(EC2Tests, self).setUp()

        credentials = _get_yaml_config(
            {
                'aws': {
                    'access_key': '<AWS-ACCESS-KEY>',
                    'secret_access_token': '<AWS-SECRET-ACCESS-TOKEN>',
                    'keyname': '<aws-us-west-2-ssh-keypair-name>',
                },
                'ssh_keys': {
                    'aws': {
                        'private_key_file': '<path-to-private-key-for-keypair>'
                    }
                }
            }
        )

        self.config = EC2Configuration(
            credentials=EC2Credentials(
                access_key_id=credentials["aws"]["access_key"],
                secret_access_key=credentials["aws"]["secret_access_token"]
            ),
            username='centos',
            disk_name='/dev/sda1',
            disk_size='48',
            instance_name='ec2-test-instance',
            tags={'name': 'test-instance-with-tags'},
            image_description='ec2-test-description',
            image_basename='ec2-image-basename',
            ami='ami-d440a6e7',
            key_filename=credentials["ssh_keys"]["aws"]["private_key_file"],
            key_pair=credentials["aws"]["keyname"],
            instance_type='t2.medium',
            security_groups=['ssh']
        ).serialize()
        self.distribution = Distribution.CENTOS7
        self.region = 'us-west-2'
        self.instance_factory = EC2Instance

if __name__ == '__main__':
    unittest.main()
