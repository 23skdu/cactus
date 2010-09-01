import unittest
import sys
import os

from sonLib.bioio import parseSuiteTestOptions
from sonLib.bioio import TestStatus
from sonLib.bioio import system
from sonLib.bioio import getTempDirectory

from cactus.shared.test import getCactusInputs_random
from cactus.shared.test import runWorkflow_multipleExamples
from cactus.shared.common import runCactusTreeStatsToLatexTables

class TestCase(unittest.TestCase):
    def testCactus_Random(self):
        tempOutputDir = getTempDirectory(".")
        runWorkflow_multipleExamples(getCactusInputs_random, 
                                     outputDir=tempOutputDir,
                                     testNumber=TestStatus.getTestSetup(), 
                                     makeCactusTreeStats=True)
        #Tests building with multiple inputs
        inputFiles = [ os.path.join(tempOutputDir, str(test), "cactusStats.xml") \
                      for test in xrange(TestStatus.getTestSetup()) ]
        regionNames = [ ("region%s" % test) for test in xrange(TestStatus.getTestSetup()) ]
        statsFileTEX = os.path.join(tempOutputDir, "cactusStats.tex")
        runCactusTreeStatsToLatexTables(inputFiles, regionNames, statsFileTEX)
        system("rm -rf %s" % tempOutputDir)
        
def main():
    parseSuiteTestOptions()
    sys.argv = sys.argv[:1]
    unittest.main()
        
if __name__ == '__main__':
    main()