/*
 * recursiveFileBuilder.c
 *
 *  Created on: 16 Mar 2012
 *      Author: benedictpaten
 */

#include <stdio.h>
#include <stdlib.h>
#include <inttypes.h>

#include "cactus.h"
#include "sonLib.h"

static void cacheNonNestedRecords(stCache *cache, stList *caps,
        char *(*segmentWriteFn)(Segment *),
        char *(*terminalAdjacencyWriteFn)(Cap *)) {
    for (int32_t i = 0; i < stList_length(caps); i++) {
        Cap *cap = stList_get(caps, i);
        while (1) {
            Cap *adjacentCap = cap_getAdjacency(cap);
            assert(adjacentCap != NULL);
            assert(cap_getCoordinate(adjacentCap) - cap_getCoordinate(cap) >= 1);
            if (cap_getCoordinate(adjacentCap) - cap_getCoordinate(cap) > 1) {
                Group *group = end_getGroup(cap_getEnd(cap));
                assert(group != NULL);
                if (group_isLeaf(group)) { //Record must be in the database already
                    char *string = terminalAdjacencyWriteFn(cap);
                    stCache_setRecord(cache, cap_getName(cap), 0, strlen(string), string);
                }
            }
            if ((cap = cap_getOtherSegmentCap(adjacentCap)) == NULL) {
                break;
            }
            Segment *segment = cap_getSegment(adjacentCap);
            char *string = segmentWriteFn(segment);
            stCache_setRecord(cache, segment_getName(segment), 0, strlen(string), string);
        }
    }
}

static stList *getNestedRecordNames(stList *caps) {
    stList *getRequests = stList_construct3(0, free);
    for (int32_t i = 0; i < stList_length(caps); i++) {
        Cap *cap = stList_get(caps, i);
        while (1) {
            Cap *adjacentCap = cap_getAdjacency(cap);
            assert(adjacentCap != NULL);
            assert(cap_getCoordinate(adjacentCap) - cap_getCoordinate(cap) >= 1);
            if (cap_getCoordinate(adjacentCap) - cap_getCoordinate(cap) > 1) {
                Group *group = end_getGroup(cap_getEnd(cap));
                assert(group != NULL);
                if (!group_isLeaf(group)) { //Record must be in the database already
                    int64_t *j = st_malloc(sizeof(int64_t));
                    j[0] = cap_getName(cap);
                    stList_append(getRequests, j);
                }
            }
            if ((cap = cap_getOtherSegmentCap(adjacentCap)) == NULL) {
                break;
            }
        }
    }
    return getRequests;
}

static void cacheNestedRecords(stKVDatabase *database, stCache *cache, stList *caps) {
    /*
     * Caches all the individual records we will concatenate into the cache structure.
     */
    stList *getRequests = getNestedRecordNames(caps);
    //Do the retrieval of the records
    stList *records = NULL;
    stTry {
        records = stKVDatabase_bulkGetRecords(database, getRequests);
    } stCatch(except) {
        stThrowNewCause(except, ST_KV_DATABASE_EXCEPTION_ID,
                                    "An unknown database error occurred when we tried to bulk get records from the database");
    } stTryEnd;
    assert(records != NULL);
    assert(stList_length(records) == stList_length(getRequests));
    //Now cache the resulting records
    while(stList_length(records) > 0) {
        stKVDatabaseBulkResult *result = stList_pop(records);
        int64_t *recordName = stList_pop(getRequests);
        int64_t recordSize;
        void *record = stKVDatabaseBulkResult_getRecord(result, &recordSize);
        stCache_setRecord(cache, *recordName, 0, recordSize, record);
        stKVDatabaseBulkResult_destruct(result); //Cleanup the memory as we go.
        free(recordName);
    }
    assert(stList_length(getRequests) == 0);
    stList_destruct(getRequests);
    stList_destruct(records);
}

static stCache *cacheRecords(stKVDatabase *database, stList *caps,
        char *(*segmentWriteFn)(Segment *),
        char *(*terminalAdjacencyWriteFn)(Cap *)) {
    stCache *cache = stCache_construct();
    cacheNestedRecords(database, cache, caps);
    cacheNonNestedRecords(cache, caps, segmentWriteFn, terminalAdjacencyWriteFn);
    return cache;
}

static void deleteNestedRecords(stKVDatabase *database, stList *caps) {
    /*
     * Caches all the individual records we will concatenate into the cache structure.
     */
    stList *deleteRequests = getNestedRecordNames(caps);
    //Do the deletion of the records
    stTry {
        stKVDatabase_bulkRemoveRecords(database, deleteRequests);
    } stCatch(except) {
        stThrowNewCause(except, ST_KV_DATABASE_EXCEPTION_ID,
                                    "An unknown database error occurred when we tried to bulk remove records from the database");
    } stTryEnd;
    stList_destruct(deleteRequests);
}

static char *getThread(stCache *cache, Cap *startCap) {
    /*
     * Iterate through, first calculating the length of the final record, then concatenating the results.
     */
    Cap *cap = startCap;
    stList *strings = stList_construct();
    while (1) { //Calculate the size of the entries in the DB that represent the thread.
        Cap *adjacentCap = cap_getAdjacency(cap);
        assert(adjacentCap != NULL);
        int64_t recordSize;
        if (cap_getCoordinate(adjacentCap) - cap_getCoordinate(cap) > 1) {
            stList_append(strings, stCache_getRecord(cache, cap_getName(cap), 0, INT64_MAX, &recordSize));
        }
        if ((cap = cap_getOtherSegmentCap(adjacentCap)) == NULL) {
            break;
        }
        stList_append(strings, stCache_getRecord(cache, segment_getName(cap_getSegment(adjacentCap)), 0, INT64_MAX, &recordSize));
    }
    char *string = stString_join2("", strings);
    stList_destruct(strings);
    return string;
}

void buildRecursiveThreads(stKVDatabase *database, stList *caps,
        char *(*segmentWriteFn)(Segment *),
        char *(*terminalAdjacencyWriteFn)(Cap *)) {
    //Cache records
    stCache *cache = cacheRecords(database, caps, segmentWriteFn, terminalAdjacencyWriteFn);

    //Build new threads
    stList *records = stList_construct3(0, (void (*)(void *))stKVDatabaseBulkRequest_destruct);
    for(int32_t i=0; i<stList_length(caps); i++) {
        Cap *cap = stList_get(caps, i);
        char *string = getThread(cache, cap);
        stKVDatabaseBulkRequest_constructInsertRequest(cap_getName(cap), string, strlen(string));
        free(string);
    }

    //Delete old records and insert new records
    deleteNestedRecords(database, caps);
    stTry {
        stKVDatabase_bulkSetRecords(database, records);
    } stCatch(except) {
        stThrowNewCause(except, ST_KV_DATABASE_EXCEPTION_ID,
                                    "An unknown database error occurred when we tried to bulk insert records from the database");
    } stTryEnd;

    //Cleanup
    stCache_destruct(cache);
    stList_destruct(records);
}

stList *buildRecursiveThreadsInList(stKVDatabase *database, stList *caps,
        char *(*segmentWriteFn)(Segment *),
        char *(*terminalAdjacencyWriteFn)(Cap *)) {
    stList *threadStrings = stList_construct3(0, free);

    //Cache records
    stCache *cache = cacheRecords(database, caps, segmentWriteFn, terminalAdjacencyWriteFn);

    //Build new threads
    for(int32_t i=0; i<stList_length(caps); i++) {
        Cap *cap = stList_get(caps, i);
        stList_append(threadStrings, getThread(cache, cap));
    }

    stCache_destruct(cache);

    return threadStrings;
}

