# vim: ai ts=4 sts=4 et sw=4 ft=python fdm=indent et

import boto.ec2

from boto.ec2.blockdevicemapping import BlockDeviceMapping, EBSBlockDeviceType
from fabric.api import env
from time import sleep
from bookshelf.api_v2.time_helpers import sleep_for_one_minute
from bookshelf.api_v2.logging_helpers import log_green, log_yellow, log_red
from bookshelf.api_v2.cloud import wait_for_ssh


def connect_to_ec2(region, access_key_id, secret_access_key):
    """ returns a connection object to AWS EC2  """
    conn = boto.ec2.connect_to_region(region,
                                      aws_access_key_id=access_key_id,
                                      aws_secret_access_key=secret_access_key)
    if conn:
        return conn
    else:
        return False


def create_ami(connection,
               region,
               instance_id,
               name,
               description,
               block_device_mapping=None,
               log=False):
    ami = connection.create_image(instance_id,
                                  name,
                                  description,
                                  block_device_mapping)

    image_status = connection.get_image(ami)
    while (image_status.state != "available" and
           image_status.state != "failed"):
        if log:
            log_yellow('creating ami...')
        sleep_for_one_minute()
        image_status = connection.get_image(ami)

    if image_status.state == "available":
        if log:
            log_green("ami %s %s" % (ami, image_status))
        return(ami)
    else:
        if log:
            log_red("ami %s %s" % (ami, image_status))
        return False


def create_server_ec2(connection,
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
    connection.create_tags([instance.id], tags)

    if log:
        log_green("Public dns: %s" % instance.public_dns_name)

    # returns our new instance
    return instance


def destroy_ebs_volume(connection, region, volume_id, log=False):
    """ destroys an ebs volume """

    if ebs_volume_exists(connection, region, volume_id):
        if log:
            log_yellow('destroying EBS volume ...')
        try:
            connection.delete_volume(volume_id)
        except:
            # our EBS volume may be gone, but AWS info tables are stale
            # wait a bit and ask again
            sleep(5)
            if not ebs_volume_exists(connection, region, volume_id):
                pass
            else:
                raise("Couldn't delete EBS volume")


def destroy_ec2(connection, region, instance_id, log=False):
    """ terminates the instance """

    data = get_ec2_info(connection=connection,
                        instance_id=instance_id,
                        region=region)

    instance = connection.terminate_instances(instance_ids=[data['id']])[0]
    if log:
        log_yellow('destroying instance ...')
    while instance.state != "terminated":
        if log:
            log_yellow("Instance state: %s" % instance.state)
        sleep(10)
        instance.update()
    volume_id = data['volume']
    if volume_id:
        destroy_ebs_volume(connection, region, volume_id)


def down_ec2(connection, instance_id, region, log=False):
    """ shutdown of an existing EC2 instance """
    # get the instance_id from the state file, and stop the instance
    instance = connection.stop_instances(instance_ids=instance_id)[0]
    while instance.state != "stopped":
        if log:
            log_yellow("Instance state: %s" % instance.state)
        sleep(10)
        instance.update()
    if log:
        log_green('Instance state: %s' % instance.state)


def ebs_volume_exists(connection, region, volume_id):
    """ finds out if a ebs volume exists """
    for vol in connection.get_all_volumes():
        if vol.id == volume_id:
            return True
    return False


def ec2():
    env.cloud = 'ec2'


def get_ec2_info(connection,
                 instance_id,
                 region,
                 username=None):
    """ queries EC2 for details about a particular instance_id
    """
    instance = connection.get_only_instances(
        filters={'instance_id': instance_id}
        )[0]

    data = instance.__dict__
    data['state'] = instance.state
    data['cloud_type'] = 'ec2'

    try:
        volume = connection.get_all_volumes(
            filters={'attachment.instance-id': instance.id}
        )[0].id
        data['volume'] = volume
    except:
        data['volume'] = ''
    return data


def up_ec2(connection,
           region,
           instance_id,
           wait_for_ssh_available=True,
           log=False,
           timeout=600):
    """ boots an existing ec2_instance """

    # boot the ec2 instance
    instance = connection.start_instances(instance_ids=instance_id)[0]
    instance.update()
    while instance.state != "running" and timeout > 1:
        log_yellow("Instance state: %s" % instance.state)
        if log:
            log_yellow("Instance state: %s" % instance.state)
        sleep(10)
        timeout = timeout - 10
        instance.update()

    # and make sure we don't return until the instance is fully up
    if wait_for_ssh_available:
        wait_for_ssh(instance.ip_address)
