try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup


setup(name='Bookshelf',
      version='2.0.4',
      description='Bookshelf of Fabric functions',
      author='ClusterHQ',
      author_email='nospam@clusterhq.com',
      url='https://www.github.com/ClusterHQ/Bookshelf/',
      packages=['bookshelf', 'bookshelf.api_v2', 'bookshelf.tests',
                'bookshelf.tests.api_v2', ],
      install_requires=['cuisine', 'fabric', 'pyrax', 'boto',
                        'google-api-python-client', 'oauth2client',
                        'zope.interface', 'enum'],
      license='Apache License, Version 2.0',
     )
