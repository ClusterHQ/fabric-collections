import socket
from time import time, sleep
import uuid


from oauth2client.client import GoogleCredentials
from googleapiclient import discovery
from googleapiclient.errors import HttpError

from bookshelf.api_v2.logging_helpers import log_green, log_yellow, log_red
from bookshelf.api_v1 import (
    wait_for_ssh, linux_distribution, os_release
)

from zope.interface import implementer, provider

from cloud_instance import ICloudInstance, ICloudInstanceFactory


@implementer(ICloudInstance)
@provider(ICloudInstanceFactory)
class GCE(object):

    cloud = 'gce'

    def __init__(self, config, distro):

        self.distro = distro

        # config
        self.project = config['project']
        self.zone = config['zone']
        self.public_key_filename = config['public_key_filename']
        self.machine_type = config['machine_type']
        self.username = config['username']

        # distro
        self.base_image_prefix = config[distro]['base_image_prefix']
        self.base_image_project = config[distro]['base_image_project']
        self.description = config[distro]['description']

        # state (set when creating from saved state)
        self.ip_address = None

        self._compute = self._get_gce_compute()

    @property
    def public_dns_name(self):
        return socket.gethostbyaddr(self.ip_address)[0]

    @classmethod
    def create_from_config(cls, config, distro):
        gce_instance = GCE(config, distro)
        # we have no state so create a new instance
        gce_instance.instance_name = u"slave-image-prep-" + unicode(uuid.uuid4())
        gce_instance._create_server()
        return gce_instance


    @classmethod
    def create_from_saved_state(cls, config, saved_state):
        # state has to include credentials, could also take config
        gce_instance = GCE(config, saved_state['distro'])
        gce_instance.instance_name = saved_state['instance_name']
        gce_instance.distro = saved_state['distro']
        gce_instance.ensure_instance_running(saved_state['instance_name'])
        # if we've restarted a terminated server, the ip address
        # might have changed from our saved state, get the
        # networking info and resave the state
        gce_instance._set_instance_networking()
        return gce_instance

    def ensure_instance_running(self, instance_name):
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
                log_red("Instance {} does not exist".format(
                    instance_name)
                )
                log_yellow("you might need to remove state file {}".format(
                    STATE_FILE_NAME)
                )
            else:
                log_red("Unknown error querying for instance {}".format(
                    instance_name)
                )
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
        instance_data = self._compute.instances().get(
            project=self.project, zone=self.zone, instance=self.instance_name
        ).execute()

        self.ip_address = (
            instance_data['networkInterfaces'][0]['accessConfigs'][0]['natIP']
        )
        wait_for_ssh(self.ip_address)

        log_green('Server has IP address {0}.'.format(self.ip_address))


    def _create_server(self):
        log_green("Started...")
        log_yellow("...Creating GCE instance...")
        latest_image = self._get_latest_image(
            self.base_image_project, self.base_image_prefix)

        self.startup_instance(self.instance_name,
                              latest_image['selfLink'],
                              disk_name=None)
        self._set_instance_networking()


    def create_image(self, image_name):
        """
        Shuts down the instance and creates and image from the disk.
        Assumes that the disk name is the same as the instance_name (this is the
        default behavior for boot disks on GCE).
        """

        disk_name = self.instance_name
        try:
            self.destroy()
        except HttpError as e:
            if e.resp.status == 404:
                log_yellow(
                    "the instance {} is already down".format(
                        self.instance_name)
                )
            else:
                raise e

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


    def down(self):
        log_yellow("downing server: {}".format(self.instance_name))
        self._wait_until_done(self._compute.instances().stop(
            project=self.project,
            zone=self.zone,
            instance=self.instance_name
        ).execute())

    def destroy(self):
        log_yellow("downing server: {}".format(self.instance_name))
        self._wait_until_done(self._compute.instances().delete(
            project=self.project,
            zone=self.zone,
            instance=self.instance_name
        ).execute())


    def _get_instance_config(self,
                            instance_name,
                            image,
                            disk_name=None):
        public_key = open(self.public_key_filename, 'r').read()
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
                    self.project, self.zone, self.machine_type)
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
                        "value": "{}:{}".format(self.username, public_key)
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
            raise RuntimeError("Creation of VM timed out or returned no result")
        log_green("Instance has booted")


    def _get_gce_compute(self):
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
        """ Gets the latest image for a distribution on gce.

        The best way to get a list of possible image_name_prefix values is to look
        at the output from ``gcloud compute images list``

        If you don't have the gcloud executable installed, it can be pip installed:
        ``pip install gcloud``

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
        # The minimum amount of data necessary to keep machine state
        # everything else can be pulled from the config

        data = {
            'ip_address': self.ip_address,
            'instance_name': self.instance_name,
            'distro': self.distro,
        }

        data['distribution'] = linux_distribution(self.username, self.ip_address)
        data['os_release'] = os_release(self.username, self.ip_address)
        return data
