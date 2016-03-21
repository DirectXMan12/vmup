from setuptools import setup

setup(name='vmup',
      version='0.1.0',
      description='A quick-and-dirty local VM provisioning tool',
      long_description=open('README.md').read(),
      author='Solly Ross',
      author_email='directxman12@gmail.com',
      license='ISC',
      url='https://github.com/directman12/vmup',
      packages=['vmup'],
      scripts=['vmup.py'],
      install_requires=['xmlmapper', 'libvirt-python', 'requests'],
      keywords='libvirt virtualization kvm',
      classifiers=[
          'Development Status :: 3 - Alpha',
          'Intended Audience :: Developers',
          'License :: OSI Approved :: ISC License (ISCL)',
          'Programming Language :: Python :: 3',
          'Programming Language :: Python :: 3.5'
      ])

