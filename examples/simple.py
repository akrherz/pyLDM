"""
Simple example, you can run this from LDM's pqact or simply test via

cat ../testdata/twoprods.txt | python simple.py

"""

from twisted.internet import reactor

from pyldm import ldmbridge


class MyIngestor(ldmbridge.LDMProductReceiver):
    def process_data(self, data):
        print("I got product", data)

    def connectionLost(self, reason):
        # Exit this program when pqact closes the STDIN PIPE
        reactor.stop()


ingest = MyIngestor()
ldm = ldmbridge.LDMProductFactory(ingest)
reactor.run()
