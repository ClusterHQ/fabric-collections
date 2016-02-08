from zope.interface import Interface, Attribute
from flufl.enum import Enum


class Distribution(Enum):
    """
    Enumeration of distributions supported by the bookshelf v3 api.

    :ivar CENTOS7: constant for CentOS 7.
    :ivar UBUNTU1404: constante for Ubuntu LTS 14.04.
    """
    CENTOS7 = u"centos7"
    UBUNTU1404 = u"ubuntu1404"


class ICloudInstanceFactory(Interface):
    """
    Interface for an object that can create cloud instances either from some
    existing serialized state, or create a new cloud instance from a
    configuration.
    """

    def create_from_config(config, distro, region):
        """
        Creates a new instance of the specified distro from the given
        configuration.

        :param dict config: An implementation-specific configuration
            dictionary. This is most likely something read in from a
            configuration language and passed directly to this layer.
        :param Distribution distro: The distribution to spin the instance up
            as.
        :param unicode region: The region to spin the instance up within.

        :return: An :class:`ICloudInstance` provider for a newly created
            instance with type distro.
        """
        pass

    def create_from_saved_state(config, saved_state):
        """
        Re-create or connect to an existing cloud instance as specified in some
        saved state and configuration.

        :param config: An opaque dict of configuration that might be specific
            to a given implementation
        :param saved_state: The serialization state of an instance created by
            serializing a previously

        :return: An :class:`ICloudInstance` provider loaded from the
            saved_state.
        """
        pass


class ICloudInstance(Interface):
    """
    Interface for interacting with a single cloud interface.
    """
    cloud_type = Attribute(
        """The name of the cloud this instance comes from.""")

    username = Attribute(
        """The username to use to log into the instance.""")

    key_filename = Attribute(
        """The filename of the private key to use to log into the instance.""")

    ip_address = Attribute(
        """Externally accessable IP address for the instance""")

    distro = Attribute(
        """The distribution on the instance. Should be one of the above """
        """Distributions.""")

    region = Attribute("""The region the instance is in.""")

    name = Attribute(
        """The human readable name of the instance.""")

    def create_image(image_name):
        """
        Creates an image from the boot disk of the instance, and leaves the
        instance in an up (booted) state.

        :param unicode image_name: The name of the image to create.

        :returns: The unique identifier of the image.
        """

    def destroy():
        """
        Downs and Destroys the instance.
        """

    def down():
        """
        Stops a running instance. Throws an exception if the instance is not
        running. Note that this must be done in a way where the instance can be
        started again.
        """

    def get_state():
        """
        Serializes this instance to a dictionary that can be passed to
        ``ICloudInstanceFactory.create_from_saved_state`` in order to
        re-create an :class:`ICloudInstance` that references the same
        underlying instance.

        :returns dict: A simple dictionary of plain old data that can be
            serialized using the JSON library.
        """
