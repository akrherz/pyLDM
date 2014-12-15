import os
from twisted.internet.test.reactormixins import ReactorBuilder

from pyldm import ldmbridge

def get_file(name):
    ''' Helper function to get the text file contents '''
    basedir = os.path.dirname(__file__)
    fn = "../testdata/%s" % (name,)
    return open(fn)


class StdioFilesTests(ReactorBuilder):

    def test_nwwsoi(self):
        reactor = self.buildReactor()

        class MyProductIngestor(ldmbridge.LDMProductReceiver):
            hits = 0
                      
            def process_data(self, buf):
                self.hits = self.hits + 1
        
            def connectionLost(self, reason):
                reactor.stop()
        

        f = get_file('nwwsoi_example.txt')
        
        ingest = MyProductIngestor(dedup=True)
        ldmbridge.LDMProductFactory( ingest, 
                                     stdin=f.fileno(),
                                     reactor=reactor )

        self.runReactor(reactor)
        self.assertEqual(ingest.hits, 1)

    def test_deduplicate(self):
        reactor = self.buildReactor()

        class MyProductIngestor(ldmbridge.LDMProductReceiver):
            hits = 0
                      
            def process_data(self, buf):
                self.hits = self.hits + 1
        
            def connectionLost(self, reason):
                reactor.stop()
        

        f = get_file('twoprods.txt')
        
        ingest = MyProductIngestor(dedup=True)
        ldmbridge.LDMProductFactory( ingest, 
                                     stdin=f.fileno(),
                                     reactor=reactor )

        self.runReactor(reactor)
        self.assertEqual(ingest.hits, 1)


    def test_bridge(self):
        reactor = self.buildReactor()

        class MyProductIngestor(ldmbridge.LDMProductReceiver):
            hits = 0
                      
            def process_data(self, buf):
                self.hits = self.hits + 1
        
            def connectionLost(self, reason):
                reactor.stop()
        

        f = get_file('twoprods.txt')
        
        ingest = MyProductIngestor()
        ldmbridge.LDMProductFactory( ingest, 
                                     stdin=f.fileno(),
                                     reactor=reactor )

        self.runReactor(reactor)
        self.assertEqual(ingest.hits, 2)
      
    def test_binarydata(self):
        reactor = self.buildReactor()

        class MyProductIngestor(ldmbridge.LDMProductReceiver):
            hits = 0
                      
            def process_data(self, buf):
                self.hits = self.hits + 1
        
            def connectionLost(self, reason):
                reactor.stop()
        

        f = get_file('threeNIDS.txt')
        
        ingest = MyProductIngestor()
        ldmbridge.LDMProductFactory( ingest, 
                                     stdin=f.fileno(),
                                     reactor=reactor )

        self.runReactor(reactor)
        self.assertEqual(ingest.hits, 3)
        
globals().update(StdioFilesTests.makeTestCaseClasses())