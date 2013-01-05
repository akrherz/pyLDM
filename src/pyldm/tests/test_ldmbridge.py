
from twisted.internet.test.reactormixins import ReactorBuilder

from pyldm import ldmbridge

class StdioFilesTests(ReactorBuilder):

    def test_bridge(self):
        reactor = self.buildReactor()

        class MyProductIngestor(ldmbridge.LDMProductReceiver):
            hits = 0
                      
            def process_data(self, buf):
                self.hits = self.hits + 1
        
            def connectionLost(self, reason):
                reactor.stop()
        

        f = file('../../data/twoprods.txt', "r")
        
        ingest = MyProductIngestor()
        ldmbridge.LDMProductFactory( ingest, 
                                     stdin=f.fileno(),
                                     reactor=reactor )

        self.runReactor(reactor)
        self.assertEqual(ingest.hits, 2)
        
globals().update(StdioFilesTests.makeTestCaseClasses())