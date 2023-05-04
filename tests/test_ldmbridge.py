"""testing via trial

    run `trial` from root directory
"""
from twisted.internet.test.reactormixins import ReactorBuilder

from pyldm import ldmbridge


def get_file(name):
    """Helper function to get the text file contents"""
    # basedir = os.path.dirname(__file__)
    fn = "../testdata/%s" % (name,)
    return open(fn, "rb")


class MyProductIngestor(ldmbridge.LDMProductReceiver):
    """hacky"""

    hits = 0

    def process_data(self, data):
        """Process data"""
        self.hits = self.hits + 1

    def connectionLost(self, reason):
        """Stop"""
        # print("Processed %s bytes" % (self.bytes_received, ))
        self.reactor.stop()


class StdioFilesTests(ReactorBuilder):
    def test_nwwsoi_dedup(self):
        """Can we DEDUP a file from NWWS-OI with multiple duplicates"""
        reactor = self.buildReactor()
        f = get_file("nwwsoi_example.txt")

        ingest = MyProductIngestor(dedup=True)
        ldmbridge.LDMProductFactory(ingest, stdin=f.fileno(), reactor=reactor)

        self.runReactor(reactor)
        assert ingest.hits == 1

    def test_deduplicate(self):
        reactor = self.buildReactor()
        f = get_file("twoprods.txt")

        ingest = MyProductIngestor(dedup=True)
        ldmbridge.LDMProductFactory(ingest, stdin=f.fileno(), reactor=reactor)

        self.runReactor(reactor)
        assert ingest.hits == 1

    def test_bridge(self):
        reactor = self.buildReactor()
        f = get_file("twoprods.txt")

        ingest = MyProductIngestor()
        ldmbridge.LDMProductFactory(ingest, stdin=f.fileno(), reactor=reactor)

        self.runReactor(reactor)
        assert ingest.hits == 2

    def test_binarydata(self):
        reactor = self.buildReactor()
        f = get_file("threeNIDS.txt")

        ingest = MyProductIngestor()
        ldmbridge.LDMProductFactory(ingest, stdin=f.fileno(), reactor=reactor)

        self.runReactor(reactor)
        assert ingest.hits == 3


globals().update(StdioFilesTests.makeTestCaseClasses())
