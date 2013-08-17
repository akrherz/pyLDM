from distutils.core import setup

import pyldm

setup(
    name='pyLDM',
    version=pyldm.__version__,
    author='daryl herzmann',
    author_email='akrherz@gmail.com',
    packages=['pyldm'],
    url='https://github.com/akrherz/pyLDM/',
    package_dir={'pyldm':'pyldm'},
    license='Apache',
    description="Python utilities to interface with Unidata's LDM.",
)