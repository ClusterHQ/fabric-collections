"""
Helpful docs for the GCE Python API
https://google-api-client-libraries.appspot.com/documentation/compute/v1/python/latest/
"""
from time import time, sleep
import uuid

from zope.interface import implementer, provider
from pyrsistent import PClass, field
from oauth2client.client import (
    SignedJwtAssertionCredentials, GoogleCredentials
)
from googleapiclient import discovery
from googleapiclient.errors import HttpError

from bookshelf.api_v2.logging_helpers import log_green, log_yellow, log_red
from bookshelf.api_v1 import wait_for_ssh
from cloud_instance import ICloudInstance, ICloudInstanceFactory, Distribution


class GCEConfiguration(PClass):
    """
    Contains all the information needed to provision a GCE instance
    and image.
    """
    credentials_private_key = field(
        factory=unicode, initial=u"", mandatory=True)
    credentials_email = field(factory=unicode, initial=u"", mandatory=True)
    public_key_filename = field(factory=unicode, mandatory=True)
    private_key_filename = field(factory=unicode, mandatory=True)
    project = field(factory=unicode, mandatory=True)
    machine_type = field(factory=unicode, mandatory=True)
    image_basename = field(type=unicode, mandatory=True, factory=unicode)
    username = field(factory=unicode, mandatory=True)
    description = field(factory=unicode, mandatory=True)
    instance_name = field(factory=unicode, mandatory=True)
    base_image_prefix = field(factory=unicode, mandatory=True)
    base_image_project = field(factory=unicode, mandatory=True)


class GCEState(PClass):
    """
    The necessary information to easily reconnect to an existing GCE
    instance.
    """
    instance_name = field(factory=unicode, mandatory=True)
    ip_address = field(factory=unicode, mandatory=True)
    distro = field(factory=unicode, mandatory=True)
    zone = field(factory=unicode, mandatory=True)


@implementer(ICloudInstance)
@provider(ICloudInstanceFactory)
class GCEInstance(object):
    """
    Class that represents a GCE instance. Provides a simple
    set of methods for interacting with that instance.

    :ivar config: a mapping object holding data that can be read into
    a GCEConfiguration object.
    :ivar GCEState state: the saved state for an existing or newly created
    instance.
    """
    cloud_type = 'gce'

    def __init__(self, config, state):
        self.config = GCEConfiguration.create(config)
        self.state = state
        self._compute = self._get_gce_compute()

    @property
    def project(self):
        return self.config.project

    @property
    def username(self):
        return self.config.username

    @property
    def zone(self):
        return self.state.zone

    @property
    def region(self):
        return self.state.zone

    @property
    def distro(self):
        return Distribution(self.state.distro)

    @property
    def description(self):
        return self.config.description

    @property
    def image_basename(self):
        return self.config.image_basename

    @property
    def ip_address(self):
        return self.state.ip_address

    @property
    def key_filename(self):
        return self.config.private_key_filename

    @classmethod
    def create_from_config(cls, config, distro, region):
        instance_name = "{}-{}".format(
            config['instance_name'],
            unicode(uuid.uuid4())
        )
        assert len(instance_name) <= 61, "Instance name too long for GCE"
        state = GCEState(
            instance_name=instance_name,
            ip_address="",
            distro=distro.value,
            zone=region
        )
        gce_instance = cls(config, state)
        gce_instance._create_server()
        return gce_instance

    @classmethod
    def create_from_saved_state(cls, config, saved_state):
        state = GCEState.create(saved_state)
        instance = cls(config, state)
        instance._ensure_instance_running(saved_state['instance_name'])
        # if we've restarted a terminated server, the ip address
        # might have changed from our saved state, get the
        # networking info and resave the state
        instance._set_instance_networking()
        return instance

    def _ensure_instance_running(self, instance_name):
        """
        If an instance is terminated but still exists (hasn't been deleted
        calling this will start the instance up again. Raises an error
        if the instance no longer exists.
        """
        try:
            instance_info = self._compute.instances().get(
                project=self.project, zone=self.zone, instance=instance_name
            ).execute()
            if instance_info['status'] == 'RUNNING':
                pass
            elif instance_info['status'] == 'TERMINATED':
                self._start_terminated_server(instance_name)
            else:
                msg = ("Instance {} is in state {}, "
                       "please start it from the console").format(
                           instance_name, instance_info['status'])
                raise Exception(msg)
            # if we've started a terminated server, re-save
            # the networking info, if we have
        except HttpError as e:
            if e.resp.status == 404:
                log_red("Instance {} does not exist".format(instance_name))
                log_yellow("you might need to remove state file.")
            else:
                log_red("Unknown error querying for instance {}".format(
                    instance_name))
            raise e

    def _start_terminated_server(self, instance_name):
        log_yellow("starting terminated instance {}".format(instance_name))
        operation = self._compute.instances().start(
            project=self.project,
            zone=self.zone,
            instance=instance_name
        ).execute()
        self._wait_until_done(operation)

    def _set_instance_networking(self):
        """
        Pulls out the IP address for the instance and double checks that
        we can connect to it's ssh port.
        """
        instance_data = self._compute.instances().get(
            project=self.project, zone=self.zone,
            instance=self.state.instance_name
        ).execute()

        ip_address = (
            instance_data['networkInterfaces'][0]['accessConfigs'][0]['natIP']
        )
        self.state = self.state.transform(['ip_address'], ip_address)
        wait_for_ssh(self.state.ip_address)
        log_green('Connected to server with IP address {0}.'.format(
            ip_address))

    def _create_server(self):
        log_green("Started...")
        log_yellow("...Creating GCE instance...")
        latest_image = self._get_latest_image(
            self.config.base_image_project, self.config.base_image_prefix)

        self.startup_instance(self.state.instance_name,
                              latest_image['selfLink'],
                              disk_name=None)
        self._set_instance_networking()

    def create_image(self, image_name):
        """
        Shuts down the instance (necessary for creating a GCE image) and
        creates and image from the disk.  Assumes that the disk name
        is the same as the instance_name (this is the default behavior
        for boot disks on GCE).
        """

        disk_name = self.state.instance_name
        self._destroy_instance()

        body = {
            "rawDisk": {},
            "name": image_name,
            "sourceDisk": "projects/{}/zones/{}/disks/{}".format(
                self.project, self.zone, disk_name
            ),
            "description": self.description
        }
        self._wait_until_done(
            self._compute.images().insert(
                project=self.project, body=body).execute()
        )
        return image_name

    def list_images(self):
        results = self._compute.images().list(project=self.project).execute()
        log_yellow("creation time\timage_name")
        for item in results['items']:
            log_green("{}\t{}".format(item['creationTimestamp'],
                                      item['name']))

    def delete_image(self, image_name):
        log_green("Deleting image {}".format(image_name))
        result = self._wait_until_done(
            self._compute.images().delete(
                project=self.project, image=image_name).execute()
        )
        log_yellow("Delete image returned status {}".format(
            result['status'])
        )

    def down(self):
        log_yellow("downing server: {}".format(self.state.instance_name))
        self._wait_until_done(self._compute.instances().stop(
            project=self.project,
            zone=self.zone,
            instance=self.state.instance_name
        ).execute())

    def _destroy_instance(self):
        log_yellow("destroying server: {}".format(self.state.instance_name))
        try:
            self._wait_until_done(self._compute.instances().delete(
                project=self.project,
                zone=self.zone,
                instance=self.state.instance_name
            ).execute())
        except HttpError as e:
            if e.resp.status == 404:
                log_yellow(
                    "the instance {} is already down".format(
                        self.state.instance_name)
                )
            else:
                raise e

    def destroy(self):
        disk_name = self.state.instance_name
        self._destroy_instance()
        try:
            self._wait_until_done(self._compute.disks().delete(
                project=self.project,
                zone=self.zone,
                disk=disk_name
            ).execute())
        except HttpError as e:
            if e.resp.status == 404:
                log_yellow(
                    "the disk {} was already destroyed".format(disk_name)
                )
            else:
                raise e

    def _get_instance_config(self,
                             instance_name,
                             image,
                             disk_name=None):
        """
        Returns the configuration dictionary used to create a new GCE
        instance.
        """
        public_key = open(self.config.public_key_filename, 'r').read()
        if disk_name:
            disk_config = {
                "type": "PERSISTENT",
                "boot": True,
                "mode": "READ_WRITE",
                "autoDelete": False,
                "source": "projects/{}/zones/{}/disks/{}".format(
                    self.project, self.zone, disk_name)
            }
        else:
            disk_config = {
                "type": "PERSISTENT",
                "boot": True,
                "mode": "READ_WRITE",
                "autoDelete": False,
                "initializeParams": {
                    "sourceImage": image,
                    "diskType": (
                        "projects/{}/zones/{}/diskTypes/pd-standard".format(
                            self.project, self.zone)
                    ),
                    "diskSizeGb": "10"
                }
            }
        gce_slave_instance_config = {
            'name': instance_name,
            'machineType': (
                "projects/{}/zones/{}/machineTypes/{}".format(
                    self.project, self.zone, self.config.machine_type)
                ),
            'disks': [disk_config],
            "networkInterfaces": [
                {
                    "network": (
                        "projects/%s/global/networks/default" % self.project
                    ),
                    "accessConfigs": [
                        {
                            "name": "External NAT",
                            "type": "ONE_TO_ONE_NAT"
                        }
                    ]
                }
            ],
            "metadata": {
                "items": [
                    {
                        "key": "sshKeys",
                        "value": "{}:{}".format(self.config.username,
                                                public_key)
                    }
                ]
            },
            'description':
                'created by: https://github.com/ClusterHQ/CI-slave-images',
            "serviceAccounts": [
                {
                    "email": "default",
                    "scopes": [
                        "https://www.googleapis.com/auth/compute",
                        "https://www.googleapis.com/auth/cloud.useraccounts.readonly",
                        "https://www.googleapis.com/auth/devstorage.read_only",
                        "https://www.googleapis.com/auth/logging.write",
                        "https://www.googleapis.com/auth/monitoring.write"
                    ]
                }
            ]
        }
        return gce_slave_instance_config

    def startup_instance(self, instance_name, image, disk_name=None):
        """
        For now, jclouds is broken for GCE and we will have static slaves
        in Jenkins.  Use this to boot them.
        """
        log_green("Started...")
        log_yellow("...Starting GCE Jenkins Slave Instance...")
        instance_config = self._get_instance_config(
            instance_name, image, disk_name
        )
        operation = self._compute.instances().insert(
            project=self.project,
            zone=self.zone,
            body=instance_config
        ).execute()
        result = self._wait_until_done(operation)
        if not result:
            raise RuntimeError(
                "Creation of VM timed out or returned no result")
        log_green("Instance has booted")

    def _get_gce_compute(self):
        if (self.config.credentials_email and
            self.config.credentials_private_key):

            credentials = SignedJwtAssertionCredentials(
                self.config.credentials_email,
                self.config.credentials_private_key,
                scope=[
                    u"https://www.googleapis.com/auth/compute",
                ]
            )
        else:
            credentials = GoogleCredentials.get_application_default()
        compute = discovery.build('compute', 'v1', credentials=credentials)
        return compute

    def _wait_until_done(self, operation):
        """
        Perform a GCE operation, blocking until the operation completes.

        This function will then poll the operation until it reaches state
        'DONE' or times out, and then returns the final operation resource
        dict.

        :param operation: A dict representing a pending GCE operation resource.

        :returns dict: A dict representing the concluded GCE operation
            resource.
        """
        operation_name = operation['name']
        if 'zone' in operation:
            zone_url_parts = operation['zone'].split('/')
            project = zone_url_parts[-3]
            zone = zone_url_parts[-1]

            def get_zone_operation():
                return self._compute.zoneOperations().get(
                    project=project,
                    zone=zone,
                    operation=operation_name
                )
            update = get_zone_operation
        else:
            project = operation['selfLink'].split('/')[-4]

            def get_global_operation():
                return self._compute.globalOperations().get(
                    project=project,
                    operation=operation_name
                )
            update = get_global_operation
        done = False
        latest_operation = None
        start = time()
        timeout = 5*60  # seconds
        while not done:
            latest_operation = update().execute()
            if (latest_operation['status'] == 'DONE' or
                    time() - start > timeout):
                done = True
            else:
                sleep(10)
                log_yellow("waiting for operation")
        return latest_operation

    def _get_latest_image(self, base_image_project, image_name_prefix):
        """
        Gets the latest image for a distribution on gce.

        The best way to get a list of possible image_name_prefix
        values is to look at the output from ``gcloud compute images
        list``

        If you don't have the gcloud executable installed, it can be
        pip installed: ``pip install gcloud``

        project, image_name_prefix examples:
        * ubuntu-os-cloud, ubuntu-1404
        * centos-cloud, centos-7
        """
        latest_image = None
        page_token = None
        while not latest_image:
            response = self._compute.images().list(
                project=base_image_project,
                maxResults=500,
                pageToken=page_token,
                filter='name eq {}.*'.format(image_name_prefix)
            ).execute()

            latest_image = next((image for image in response.get('items', [])
                                 if 'deprecated' not in image),
                                None)
            page_token = response.get('nextPageToken')
            if not page_token:
                break
        return latest_image

    def get_state(self):
        """
        The minimum amount of data necessary (plus a little more for ease
        of use) to keep machine state everything else can be pulled
        from the config.
        """
        data = {
            'ip_address': self.state.ip_address,
            'instance_name': self.state.instance_name,
            'distro': self.state.distro,
            'zone': self.state.zone,
        }
        return data
