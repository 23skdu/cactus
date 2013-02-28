#!/usr/bin/env python
from twisted.python.dist import resultfiles

#Copyright (C) 2009-2011 by Benedict Paten (benedictpaten@gmail.com)
#
#Released under the MIT license, see LICENSE.txt
#!/usr/bin/env python

"""Script for running an all against all (including self) set of alignments on a set of input
sequences. Uses the jobTree framework to parallelise the blasts.
"""
import os
import sys
from optparse import OptionParser
from bz2 import BZ2File

from sonLib.bioio import TempFileTree
from sonLib.bioio import getTempDirectory
from sonLib.bioio import getTempFile
from sonLib.bioio import logger
from sonLib.bioio import system
from sonLib.bioio import fastaRead
from sonLib.bioio import fastaWrite
from sonLib.bioio import getLogLevelString

from jobTree.scriptTree.target import Target
from jobTree.scriptTree.stack import Stack

class BlastOptions:
    def __init__(self, chunkSize, overlapSize, 
                 lastzArguments, chunksPerJob, compressFiles, memory):
        """Method makes options which can be passed to the to the make blasts target.
        """
        self.chunkSize = chunkSize
        self.overlapSize = overlapSize
        self.blastString = "lastz --format=cigar %s SEQ_FILE_1[multiple][nameparse=darkspace] SEQ_FILE_2[nameparse=darkspace] > CIGARS_FILE"  % lastzArguments 
        self.selfBlastString = "lastz --format=cigar %s SEQ_FILE[multiple][nameparse=darkspace] SEQ_FILE[multiple][nameparse=darkspace] --notrivial > CIGARS_FILE" % lastzArguments
        self.compressFiles = compressFiles
        self.memory = memory

def makeStandardBlastOptions():
    """Function to create options for a pecan2_batch.MakeBlasts target for middle level 
    alignments (20 MB range)
    """
    chunkSize = 10000000
    overlapSize = 10000
    chunksPerJob = 1
    compressFiles = True
    lastzArguments=""
    memory = sys.maxint
    return BlastOptions(chunkSize=chunkSize, overlapSize=overlapSize,
                                lastzArguments=lastzArguments,
                                chunksPerJob=chunksPerJob, compressFiles=compressFiles, memory=memory)
    
class BlastFlower(Target):
    """Take a reconstruction problem and generate the sequences to be blasted.
    Then setup the follow on blast targets and collation targets.
    """
    def __init__(self, cactusDisk, flowerName, resultsFile, blastOptions, minimumSequenceLength=1):
        Target.__init__(self)
        self.cactusDisk = cactusDisk
        self.flowerName = flowerName
        self.resultsFile = resultsFile
        self.blastOptions = blastOptions
        self.minimumSequenceLength = minimumSequenceLength
        
    def run(self):
        ##########################################
        #Construct the sequences file for doing all against all blast.
        ##########################################
        
        tempSeqFile = os.path.join(self.getGlobalTempDir(), "cactusSequences.fa")
        system("cactus_blast_chunkSequences '%s' %s %s %s %s" % (self.cactusDisk, self.flowerName, tempSeqFile, 
                                                         self.minimumSequenceLength, getLogLevelString()))
        logger.info("Got the sequence files to align")
        
        ##########################################
        #Make blast target
        ##########################################
        
        self.addChildTarget(self.blastOptions.makeBlastOptions([ tempSeqFile ], self.resultsFile))
        logger.info("Added child target okay")

class MakeBlasts(Target):
    """Breaks up the inputs into bits and builds a bunch of alignment jobs.
    """
    def __init__(self, options, sequences, finalResultsFile):
        Target.__init__(self)
        assert options.chunkSize > options.overlapSize
        assert options.overlapSize >= 2
        self.options = options
        self.sequences = sequences
        self.finalResultsFile = finalResultsFile
        
    def run(self):
        
        ##########################################
        #Break up the fasta sequences into overlapping chunks.
        ##########################################
        
        chunksDir = os.path.join(self.self.getGlobalTempDir(), "chunks")
        if not os.path.exists(chunksDir):
            os.mkdir(chunksDir)
        chunks = [ line.split()[0] for line in popenCatch("cactus_blast_chunkSequences %i %i %s" % \
               (self.options.chunkSize, self.options.overlapSize,
                chunksDir)) ]
        logger.info("Broken up the sequence files into individual 'chunk' files")
    
        ##########################################
        #Make all against all blast jobs lists for non overlapping files.
        ##########################################
       
        #Avoid compression if just one chunk
        self.options.compressFiles = self.options.compressFiles and len(chunks) > 2
        selfResultsDir = os.path.join(self.getGlobalTempDir(), "selfResults")
        if not os.path.exists(selfResultsDir):
            os.mkdir(selfResultsDir)
        resultsFiles = []
        for i in xrange(len(chunks)):
            resultsFile = os.path.join(selfResultsDir, str(i))
            resultsFiles.append(resultsFile)
            self.addChildTarget(RunSelfBlast(self.options, seqFiles, resultsFile))
        logger.info("Made the list of self blasts")
        
         ##########################################
         #Make follow on job to do all-against-all blast
         ##########################################
    
        self.setFollowOnTarget(MakeBlasts2(self.options, chunks, resultFiles, self.finalResultFile))
    
class MakeBlasts2(Target):
        def __init__(self, options, chunks, resultsFiles, finalResultsFile):
            self.options = options
            self.chunks = chunks
            self.resultsFiles = resultsFiles
            self.finalResultsFile = finalResultsFile
        
        def run(self):
            tempFileTree = TempFileTree(os.path.join(self.getGlobalTempDir(), "allAgainstAllResults"))
            #Make the list of blast jobs.
            for i in xrange(0, len(self.chunks)):
                for j in xrange(i+1, len(self.chunks)):
                    resultsFile = tempFileTree.getTempFile()
                    self.resultsFiles.append(resultsFile)
                    self.addChildTarget(RunBlast(self.options, chunks[i], chunks[j], resultsFile))
            logger.info("Made the list of all-against-all blasts")
            #Set up the job to collate all the results
            self.setFollowOnTarget(CollateBlasts(self.options, self.finalResultsFile, self.resultsFiles))
        
def compressFastaFile(fileName):
    """Compressed
    """
    fileHandle = BZ2File(fileName + ".bz2", 'w')
    fileHandle2 = open(fileName, 'r')
    for fastaHeader, seq in fastaRead(fileHandle2):
        fastaWrite(fileHandle, fastaHeader, seq)
    fileHandle2.close()
    fileHandle.close()
        
class RunSelfBlast(Target):
    """Runs blast as a job.
    """
    def __init__(self, options, seqFile, resultsFile):
        Target.__init__(self, memory=options.memory)
        self.options = options
        self.seqFile = seqFile
        self.resultsFile = resultsFile
    
    def run(self):   
        tempResultsFile = os.path.join(self.getLocalTempDir(), "tempResults.cig")
        command = selfBlastString.replace("CIGARS_FILE", tempResultsFile).replace("SEQ_FILE", seqFile)
        system(command)
        system("cactus_blast_convertCoordinates %s %s" % (tempResultsFile, self.resultsFile))
        if self.options.compressFiles:
            compressFastaFile(self.seqFile)
        logger.info("Ran the self blast okay")

def decompressFastaFile(fileName, tempDir):
    """Copies the file from the central dir to a temporary file, returning the temp file name.
    """
    tempFileName = getTempFile(suffix=".fa", rootDir=tempDir)
    fileHandle = open(tempFileName, 'w')
    fileHandle2 = BZ2File(fileName, 'r')
    for fastaHeader, seq in fastaRead(fileHandle2):
        fastaWrite(fileHandle, fastaHeader, seq)
    fileHandle2.close()
    fileHandle.close()
    return tempFileName

class RunBlast(Target):
    """Runs blast as a job.
    """
    def __init__(self, options, seqFile1, seqFile2, resultsFile):
        Target.__init__(self, memory=options.memory)
        self.options = options
        self.seqFile1 = seqFile1
        self.seqFile2 = seqFile2
        self.resultsFile = resultsFile
    
    def run(self):
        if self.options.compressFiles:
            self.seqFile1 = decompressFastaFile(self.seqFile1 + ".bz2", self.getLocalTempDir())
            self.seqFile2 = decompressFastaFile(self.seqFile2 + ".bz2", self.getLocalTempDir())
        tempResultsFile = os.path.join(self.getLocalTempDir(), "tempResults.cig")
        command = blastString.replace("CIGARS_FILE", tempResultsFile).replace("SEQ_FILE_1", self.seqFile1).replace("SEQ_FILE_2", self.seqFile2)
        system(command)
        system("cactus_blast_convertCoordinates %s %s" % (tempResultsFile, self.resultsFile))
        logger.info("Ran the blast okay")

def catFiles(filesToCat, catFile):
    """Cats a bunch of files into one file. Ensures a no more than MAX_CAT files
    are concatenated at each step.
    """
    MAX_CAT = 25
    system("cat %s > %s" % (" ".join(filesToCat[:MAX_CAT]), catFile))
    filesToCat = filesToCat[MAX_CAT:]
    while len(filesToCat) > 0:
        system("cat %s >> %s" % (" ".join(filesToCat[:MAX_CAT]), catFile))
        filesToCat = filesToCat[MAX_CAT:]
    
class CollateBlasts(Target):
    """Collates all the blasts into a single alignments file.
    """
    def __init__(self, options, finalResultsFile, resultsFiles):
        Target.__init__(self)
        self.options = options
        self.finalResultsFile = finalResultsFile
        self.resultsFiles = resultsFiles
    
    def run(self):
        catFiles(self.resultsFiles, self.finalResultsFile)
        logger.info("Collated the alignments to the file: %s",  self.finalResultsFile)

def main():
    ##########################################
    #Construct the arguments.
    ##########################################    
    
    parser = OptionParser()
    Stack.addJobTreeOptions(parser)
    options = makeStandardBlastOptions()
    
    #output stuff
    parser.add_option("--cigars", dest="cigarFile", 
                      help="File to write cigars in",
                      default="cigarFile.txt")
    
    parser.add_option("--chunkSize", dest="chunkSize", type="int", 
                     help="The size of chunks passed to lastz (must be at least twice as big as overlap)",
                     default=options.chunkSize)
    
    parser.add_option("--overlapSize", dest="overlapSize", type="int",
                     help="The size of the overlap between the chunks passed to lastz (min size 2)",
                     default=options.overlapSize)
    
    parser.add_option("--blastString", dest="blastString", type="string",
                     help="The default string used to call the blast program. \
Must contain three strings: SEQ_FILE_1, SEQ_FILE_2 and CIGARS_FILE which will be \
replaced with the two sequence files and the results file, respectively",
                     default=options.blastString)
    
    parser.add_option("--selfBlastString", dest="selfBlastString", type="string",
                     help="The default string used to call the blast program for self alignment. \
Must contain three strings: SEQ_FILE and CIGARS_FILE which will be \
replaced with the the sequence file and the results file, respectively",
                     default=options.selfBlastString)
    
    parser.add_option("--chunksPerJob", dest="chunksPerJob", type="int",
                      help="The number of blast chunks to align per job. Every chunk is aligned against every other chunk, \
this allows each job to more than one chunk comparison per job, which will save on I/O.", default=options.chunksPerJob)
    
    parser.add_option("--compressFiles", dest="compressFiles", action="store_false",
                      help="Turn of bz2 based file compression of sequences for I/O transfer", 
                      default=options.compressFiles)
    
    parser.add_option("--lastzMemory", dest="memory", type="int",
                      help="Lastz memory (in bytes)", 
                      default=sys.maxint)
    
    options, args = parser.parse_args()
    
    firstTarget = MakeBlasts(options, args, options.cigarFile)
    Stack(firstTarget).startJobTree(options)

def _test():
    import doctest 
    return doctest.testmod()

if __name__ == '__main__':
    from cactus.blastAlignment.cactus_batch import *
    _test()
    main()
