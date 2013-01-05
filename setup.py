from distutils.core import setup

setup(
    name='pyLDM',
    version='0.0.1',
    author='daryl herzmann',
    author_email='akrherz@gmail.com',
    packages=['pyldm'],
    url='https://github.com/akrherz/pyLDM/',
    package_dir={'pyldm':'src/pyldm'},
    license='Apache',
    description="Python utilities to interface with Unidata's LDM.",
)