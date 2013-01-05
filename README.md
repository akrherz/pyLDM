pyLDM
=====

This is a collection of Twisted Python code that connects you to LDM. At this
time, the only available interface is an asynchronos PIPE to pqact.  Here is 
an example pqact.conf entry.

    IDS "/pTOR"
        PIPE python myingestor.py

and the associated myingestor file

    from pyldm import ldmbridge
    from twisted.internet import reactor
    
    class MyIngestor(ldmbridge.LDMProductReceiver):
        def process_data(self, data):
            print 'I got product', data
            
    ingest = MyIngestor()
    ldm = ldmbridge.LDMProductFactory(ingest)
    reactor.run()

The myingestor.py python script will keep running as long as pqact keeps the PIPE open to the process!
