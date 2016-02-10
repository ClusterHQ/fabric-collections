from sys import exit
from time import sleep
import uuid

from zope.interface import implementer, provider
from pyrsistent import PClass, field
import pyrax
#from novaclient.servers import REBOOT_HARD
from novaclient.exceptions import NotFound
from fabric.api import sudo, settings
from fabric.context_managers import hide

from bookshelf.api_v1 import wait_for_ssh
from bookshelf.api_v2.logging_helpers import log_green, log_yellow, log_red
from cloud_instance import ICloudInstance, ICloudInstanceFactory, Distribution


class RackspaceConfiguration(PClass):
    username = field(factory=unicode, mandatory=True)
    instance_type = field(factory=unicode, mandatory=True)
    key_pair = field(factory=unicode, mandatory=True)
    public_key_filename = field(factory=unicode, mandatory=True)
    private_key_filename = field(factory=unicode, mandatory=True)
    image_basename = field(type=unicode, mandatory=True, factory=unicode)
    access_key_id = field(factory=unicode, mandatory=True)
    secret_access_key = field(factory=unicode, mandatory=True)
    ami = field(factory=unicode, mandatory=True)
    description = field(factory=unicode, mandatory=True)
    instance_name = field(factory=unicode, mandatory=True)


class RackspaceState(PClass):
    instance_name = field(factory=unicode, mandatory=True)
    ip_address = field(factory=unicode, mandatory=True)
    distro = field(factory=unicode, mandatory=True)
    region = field(factory=unicode, mandatory=True)


@implementer(ICloudInstance)
@provider(ICloudInstanceFactory)
class RackspaceInstance(object):

    cloud_type = 'rackspace'

    def __init__(self, config, state):
        self.config = RackspaceConfiguration.create(config)
        self.state = state
        self._nova = self._connect_to_rackspace()

    @property
    def distro(self):
        return Distribution(self.state.distro)

    @property
    def username(self):
        return self.config.username

    @property
    def description(self):
        return self.config.description

    @property
    def ip_address(self):
        return self.state.ip_address

    @property
    def image_basename(self):
        return self.config.image_basename

    @property
    def region(self):
        return self.state.region

    @property
    def key_filename(self):
        return self.config.private_key_filename

    @classmethod
    def create_from_config(cls, config, distro, region):
        distro = distro.value
        instance_name = "{}-{}".format(
            config['instance_name'],
            unicode(uuid.uuid4())
        )
        state = RackspaceState(
            instance_name=instance_name,
            ip_address="",
            distro=distro,
            region=region
        )
        instance = cls(config, state)
        instance.upload_key()
        instance._create_server()
        return instance

    def upload_key(self):
        try:
            log_green("Checking for key pair {}".format(self.config.key_pair))
            self._nova.keypairs.get(self.config.key_pair)
            log_green("Key pair exists in rackspace")
        except NotFound:
            log_green("Creating key pair {}".format(self.config.key_pair))
            with open(self.config.public_key_filename) as keyfile:
                self._nova.keypairs.create(self.config.key_pair,
                                           keyfile.read())

    @classmethod
    def create_from_saved_state(cls, config, saved_state):
        state = RackspaceState.create(saved_state)
        instance = cls(config, state)
        server = instance._nova.servers.find(name=instance.state.instance_name)
        # if we've restarted a terminated server, the ip address
        # might have changed from our saved state, get the
        # networking info and resave the state
        instance._set_instance_networking(server)
        return instance

    def _create_server(self):
        log_yellow("Creating Rackspace instance...")
        flavor = self._nova.flavors.find(name=self.config.instance_type)
        image = self._nova.images.find(name=self.config.ami)
        server = self._nova.servers.create(
            name=self.state.instance_name,
            flavor=flavor.id,
            image=image.id,
            region=self.state.region,
            availability_zone=self.state.region,
            key_name=self.config.key_pair
        )

        while server.status == 'BUILD':
            log_yellow("Waiting for build to finish...")
            sleep(10)
            server = self._nova.servers.get(server.id)
        # check for errors
        if server.status != 'ACTIVE':
            log_red("Error creating rackspace instance")
            exit(1)
        self._set_instance_networking(server)

    def _set_instance_networking(self, server):
        ip_address = server.accessIPv4
        if ip_address is None:
            log_red('No IP address assigned')
            exit(1)
        self.state = self.state.transform(['ip_address'], ip_address)
        wait_for_ssh(ip_address)
        log_green('Connected to server with IP address {0}.'.format(
            ip_address)
        )

    def _connect_to_rackspace(self):
        """ returns a connection object to Rackspace  """
        pyrax.set_setting('identity_type', 'rackspace')
        pyrax.set_default_region(self.state.region)
        pyrax.set_credentials(self.config.access_key_id,
                              self.config.secret_access_key)
        nova = pyrax.connect_to_cloudservers(region=self.state.region)
        return nova

    def create_image(self, image_name):
        server = self._nova.servers.find(name=self.state.instance_name)
        image_id = self._nova.servers.create_image(server.id,
                                                   image_name=image_name)
        image = self._nova.images.get(image_id).status.lower()
        log_green('creating rackspace image...')
        sleep_time = 20
        elapsed = 0
        while self._nova.images.get(image_id).status.lower() not in ['active',
                                                                     'error']:
            log_green('building rackspace image, '
                      'this could take a bit: elapsed {}s.'.format(elapsed))
            sleep(20)
            elapsed += sleep_time
        if image == 'error':
            log_red('error creating image')
            exit(1)

        log_green('finished image: %s' % image_id)
        return image_id

    def list_images(self):
        images = self._nova.images.list()
        log_yellow("creation time\timage_name\timage_id")
        for image in sorted(images, key=lambda x: x.created):
            log_green("{}\t{:50}\t{}".format(
                image.created, image.human_id, image.id)
            )

    def delete_image(self, image_id):
        return self._nova.images.delete(image_id)

    def destroy(self):
        server = self._nova.servers.find(name=self.state.instance_name)
        log_yellow('deleting rackspace instance ...')
        server.delete()

        try:
            while True:
                server = self._nova.servers.get(server.id)
                log_yellow('waiting for deletion ...')
                sleep(5)
        except NotFound:
            pass
        log_green('The server has been deleted')

    def down(self):
        """
        Not raising an exception here because I'm a pushover, settle for
        just yelling loudly at the user.
        """
        log_red("Rackspace instances can't be downed, just keeping it running")
        log_red("/Tableflip :( ")

    def get_state(self):
        """
        Get the state that's needed to reconnect to the instance
        """
        data = {
            'instance_name': self.state.instance_name,
            'ip_address': self.state.ip_address,
            'distro': self.state.distro,
            'region': self.state.region,
        }
        return data
