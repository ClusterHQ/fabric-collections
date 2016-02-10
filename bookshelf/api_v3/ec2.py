
from time import sleep

import boto.ec2
from boto.ec2.blockdevicemapping import BlockDeviceMapping, EBSBlockDeviceType
from pyrsistent import PClass, field, pmap, PMap, pvector, PVector
from zope.interface import implementer, provider

from bookshelf.api_v3.cloud_instance import (
    ICloudInstance, ICloudInstanceFactory, Distribution
)

from bookshelf.api_v2.logging_helpers import log_green, log_yellow, log_red
from bookshelf.api_v2.cloud import wait_for_ssh


class EC2State(PClass):
    """
    Information about the ec2 instance that will later be used to
    reconnecto to the instance.
    """
    instance_id = field(type=unicode, mandatory=True, factory=unicode)
    region = field(type=unicode, mandatory=True, factory=unicode)
    distro = field(mandatory=True, factory=Distribution,
                   serializer=lambda _, x: x.value)


class EC2Credentials(PClass):
    """
    The credentials needed to authenticate with EC2.
    """
    access_key_id = field(type=unicode, mandatory=True, factory=unicode)
    secret_access_key = field(type=unicode, mandatory=True, factory=unicode)


def _parse_unicode_pmap(d):
    return pmap({unicode(k): unicode(v)
                 for k, v in d.iteritems()})


def _parse_unicode_pvector(l):
    return pvector([unicode(x) for x in l])


class EC2Configuration(PClass):
    """
    The configuration needed to create an EC2 instance and image
    """
    credentials = field(type=EC2Credentials, mandatory=True)
    username = field(type=unicode, mandatory=True, factory=unicode)
    instance_name = field(type=unicode, mandatory=True, factory=unicode)
    tags = field(type=PMap, mandatory=True, factory=_parse_unicode_pmap)
    image_description = field(type=unicode, mandatory=True, factory=unicode)
    image_basename = field(type=unicode, mandatory=True, factory=unicode)
    ami = field(type=unicode, mandatory=True, factory=unicode)
    key_filename = field(type=unicode, mandatory=True, factory=unicode)
    key_pair = field(type=unicode, mandatory=True, factory=unicode)
    instance_type = field(type=unicode, mandatory=True, factory=unicode)
    disk_name = field(type=unicode, mandatory=True, factory=unicode)
    disk_size = field(type=int, mandatory=True, factory=int)
    security_groups = field(type=PVector, mandatory=True,
                            factory=_parse_unicode_pvector)


def _connect_to_ec2(region, credentials):
    """
    :param region: The region of AWS to connect to.
    :param EC2Credentials credentials: The credentials to use to authenticate
        with EC2.

    :return: a connection object to AWS EC2
    """
    conn = boto.ec2.connect_to_region(
        region,
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key
    )
    if conn:
        return conn
    else:
        log_red('Failure to authenticate to EC2.')
        return False


def _create_server_ec2(connection,
                       region,
                       disk_name,
                       disk_size,
                       ami,
                       key_pair,
                       instance_type,
                       tags={},
                       security_groups=None,
                       delete_on_termination=True,
                       log=False,
                       wait_for_ssh_available=True):
    """
    Creates EC2 Instance
    """

    if log:
        log_green("Started...")
        log_yellow("...Creating EC2 instance...")

    ebs_volume = EBSBlockDeviceType()
    ebs_volume.size = disk_size
    bdm = BlockDeviceMapping()
    bdm[disk_name] = ebs_volume

    # get an ec2 ami image object with our choosen ami
    image = connection.get_all_images(ami)[0]
    # start a new instance
    reservation = image.run(1, 1,
                            key_name=key_pair,
                            security_groups=security_groups,
                            block_device_map=bdm,
                            instance_type=instance_type)

    # and get our instance_id
    instance = reservation.instances[0]

    #  and loop and wait until ssh is available
    while instance.state == u'pending':
        if log:
            log_yellow("Instance state: %s" % instance.state)
        sleep(10)
        instance.update()
    if log:
        log_green("Instance state: %s" % instance.state)
    if wait_for_ssh_available:
        wait_for_ssh(instance.public_dns_name)

    # update the EBS volumes to be deleted on instance termination
    if delete_on_termination:
        for dev, bd in instance.block_device_mapping.items():
            instance.modify_attribute('BlockDeviceMapping',
                                      ["%s=%d" % (dev, 1)])

    # add a tag to our instance
    if tags:
        connection.create_tags([instance.id], tags)

    if log:
        log_green("Public dns: %s" % instance.public_dns_name)

    # returns our new instance
    return instance


@implementer(ICloudInstance)
@provider(ICloudInstanceFactory)
class EC2Instance():
    """
    Class representing an EC2 instance, that provides methods for interacting
    with the instance.

    :ivar connection: A boto connection object to interact with ec2.
    :ivar instance: A boto instance object.
    :ivar config: An EC2Configuration describing the configuration for this EC2
        instance.
    :ivar state: An EC2State describing this EC2 instance.
    """
    def __init__(self, instance, connection, config, state):
        self.connection = connection
        self.instance = instance
        self.config = config
        self.state = state

    cloud_type = u'ec2'

    @property
    def ip_address(self):
        return self.instance.ip_address

    @property
    def image_basename(self):
        return self.config.image_basename

    @property
    def username(self):
        return self.config.username

    @property
    def key_filename(self):
        return self.config.key_filename

    @property
    def distro(self):
        return self.state.distro

    @property
    def region(self):
        return self.state.region

    @classmethod
    def create_from_config(cls, config, distro, region):
        parsed_config = EC2Configuration.create(config)
        connection = _connect_to_ec2(
            region=region,
            credentials=parsed_config.credentials
        )
        instance = _create_server_ec2(
            connection=connection,
            region=region,
            disk_name=parsed_config.disk_name,
            disk_size=parsed_config.disk_size,
            ami=parsed_config.ami,
            key_pair=parsed_config.key_pair,
            instance_type=parsed_config.instance_type,
            tags=parsed_config.tags,
            security_groups=parsed_config.security_groups,
            delete_on_termination=True,
            log=False,
            wait_for_ssh_available=True
        )
        state = EC2State(
            instance_id=instance.id,
            region=region,
            distro=distro,
        )

        return cls(
            connection=connection,
            instance=instance,
            config=parsed_config,
            state=state
        )

    @classmethod
    def create_from_saved_state(cls, config, saved_state, timeout=600):
        parsed_config = EC2Configuration.create(config)
        state = EC2State.create(saved_state)
        connection = _connect_to_ec2(
            region=state.region,
            credentials=parsed_config.credentials
        )

        instance = connection.start_instances(
            instance_ids=state.instance_id)[0]
        instance.update()
        while instance.state != "running" and timeout > 1:
            log_yellow("Instance state: %s" % instance.state)
            sleep(10)
            timeout = timeout - 10
            instance.update()

        # and make sure we don't return until the instance is fully up
        wait_for_ssh(instance.ip_address)
        return cls(
            connection=connection,
            instance=instance,
            config=parsed_config,
            state=state
        )

    def create_image(self, image_name):
        ami = self.connection.create_image(
            self.state.instance_id,
            image_name,
            description=self.config.image_description,
        )

        image_status = self.connection.get_image(ami)
        while (image_status.state != "available" and
               image_status.state != "failed"):
            log_yellow('creating ami...')
            sleep(60)
            image_status = self.connection.get_image(ami)

        if image_status.state == "available":
            log_green("ami %s %s" % (ami, image_status))
            return(ami)
        else:
            log_red("ami %s %s" % (ami, image_status))
            return False

    def list_images(self):
        images = self.connection.get_all_images(owners='self')
        log_yellow("creation time\timage_name\timage_id")
        for image in sorted(images, key=lambda x: x.creationDate):
            log_green("{}\t{:50}\t{}".format(
                image.creationDate, image.name, image.id)
            )

    def delete_image(self, image_id):
        images = self.connection.get_all_images(owners='self')
        found = False
        for image in images:
            if image.id == image_id:
                log_yellow("Deleting image {}".format(image_id))
                image.deregister(delete_snapshot=True)
                found = True
                break
        if not found:
            log_red("Could not find image {}".format(image_id))

    def _ebs_volume_exists(self, volume_id):
        """ finds out if a ebs volume exists """
        for vol in self.connection.get_all_volumes():
            if vol.id == volume_id:
                return True
        return False

    def _destroy_ebs_volume(self, volume_id):
        """ destroys an ebs volume """

        if self._ebs_volume_exists(volume_id):
            log_yellow('destroying EBS volume ...')
            try:
                self.connection.delete_volume(volume_id)
            except:
                # our EBS volume may be gone, but AWS info tables are stale
                # wait a bit and ask again
                sleep(5)
                if not self._ebs_volume_exists(volume_id):
                    pass
                else:
                    raise("Couldn't delete EBS volume")

    def destroy(self):
        self.down()
        volumes = self.connection.get_all_volumes(
            filters={'attachment.instance-id': self.state.instance_id}
        )
        instance = self.connection.terminate_instances(
            instance_ids=[self.state.instance_id])[0]
        log_yellow('destroying instance ...')
        while instance.state != "terminated":
            log_yellow("Instance state: %s" % instance.state)
            sleep(10)
            instance.update()
        for volume in volumes:
            self._destroy_ebs_volume(volume.id)

    def down(self):
        instance = self.connection.stop_instances(
            instance_ids=self.state.instance_id)[0]
        while instance.state != "stopped":
            log_yellow("Instance state: %s" % instance.state)
            sleep(10)
            instance.update()
        log_green('Instance state: %s' % instance.state)

    def get_state(self):
        return self.state.serialize()
