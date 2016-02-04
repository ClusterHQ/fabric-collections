from zope.interface import Interface

STATE_FILE_NAME = '.state.json'


class ICloudInstance(Interface):
    # @classmethod
    # def create_from_config(config, region, distro):
    #     """
    #     Uses config to create a new ICloudInstance. Creates the VM as
    #     well***
    #     """
    # @classmethod
    # def create_from_state():
    #     pass

    def create_image(name, description):
        """Creates an image and leaves it in an up state
        """
    def destroy():
        """
        Destroys an existing image
        """

    def down():
        """
        Stops a running image. Throws an exception if the image is not
        running
        """
    def up():
        """
        starts an existing image. Throws an exception if the image is
        already running.
        """

    def serialize_to_state():
        """
        Saves information about this image to a json file that will be used
        on susequent fab runs.
        """
