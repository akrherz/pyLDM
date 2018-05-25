pyLDM
=====

This is a collection of **Twisted Python** code that connects you to LDM. At this
time, the only available interface is an asynchronos PIPE to pqact.  Here is 
an example pqact.conf entry.

    IDS "/pTOR"
        PIPE python myingestor.py

and the associated myingestor file

```python
    from twisted.internet import reactor
    from pyldm import ldmbridge
    
    class MyIngestor(ldmbridge.LDMProductReceiver):
        def process_data(self, data):
            print('I got product!')

        def connectionLost(self, reason):
            # Exit this program when pqact closes the STDIN PIPE
            reactor.stop()
            
    ingest = MyIngestor()
    ldm = ldmbridge.LDMProductFactory(ingest)
    reactor.run()
```

The myingestor.py python script will keep running as long as pqact keeps the PIPE open to the process!
