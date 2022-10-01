#!/usr/bin/python3

import requests
from timeit import default_timer as timer
from bs4 import BeautifulSoup
from requests_toolbelt.utils import dump

import argparse
import sys, re, json
from pathlib import Path
from dataclasses import dataclass
import csv

@dataclass
class BibEntry:
    """A bibliographic entry. Note that multiple entries from different bibsources may coexist"""
    idx: int
    title: str
    authors: str
    date: str
    source: str
    doi: str
    bibsource: str
    pubtype: str
    abstract: str 

class DoiCache:
    """A simple cache for DOI objects; must be saved-to explicitly"""

    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.load()

    def has(self, q):
        return q in self.data

    def get(self, q):
        return self.data[q]

    def put(self, q, v):
        self.data[q] = v
        return v

    def load(self):
        if (Path(self.cache_file).is_file()):
            with open(self.cache_file, 'r') as cache_f:
                self.data = json.load(cache_f)
            print(f"... DOI cache initialized from {self.cache_file} with {len(self.data)} entries")
        else:
            print("... no DOI cache file, creating one")
            self.data = {}
            self.save()

    def save(self):
        with open(self.cache_file, 'w') as cache_f:
            json.dump(self.data, cache_f, sort_keys=False, indent=2)
        print(f"... DOI cache at {self.cache_file} updated: now has {len(self.data)} entries")

class ReqCache:
    """A simple cache to avoid hammering servers with duplicate requests. Cache misses result in requests."""

    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.load()
        self.session = requests.Session()

    def get(self, q):
        print(f'getting {q} ...')
        if (q not in self.data):
            start = timer()
            r = self.session.get(q)
            end = timer()
            print(f'--> {r.status_code} in {end - start} seconds') 
            if (r.status_code == 200):
                self.data[q] = r.text
                self.save()
            else:
                raise Exception(f"Bad result for {q}: {r.text}")
        else:
            print(f'... in cache')
        return self.data[q]           

    def post(self, q, d):
        qd = q + json.dumps(d, separators=(',', ':'))
        print(f'posting {qd} ...')
        if (qd not in self.data):
            start = timer()
            r = self.session.post(q, d)
            print(dump.dump_all(r).decode("utf-8"))
            end = timer()
            print(f'--> {r.status_code} in {end - start} seconds') 
            if (r.status_code == 200):
                self.data[qd] = r.text
                self.save()
            else:
                raise Exception(f"Bad result for {qd}: {r.text}")
        else:
            print(f'... in cache')
        return self.data[qd]              

    def post_json(self, q, d):
        qd = q + json.dumps(d, separators=(',', ':'))
        print(f'posting {qd} ...')
        if (qd not in self.data):
            start = timer()
            r = self.session.post(q, json=d)
            print(dump.dump_all(r).decode("utf-8"))
            end = timer()
            print(f'--> {r.status_code} in {end - start} seconds') 
            if (r.status_code == 200):
                self.data[qd] = r.text
                self.save()
            else:
                raise Exception(f"Bad result for {qd}: {r.text}")
        else:
            print(f'... in cache')
        return self.data[qd]          

    def load(self):
        if (Path(self.cache_file).is_file()):
            with open(self.cache_file, 'r') as cache_f:
                self.data = json.load(cache_f)
            print(f"... cache initialized from {self.cache_file} with {len(self.data)} entries")
        else:
            print("... no cache file, creating one")
            self.data = {}
            self.save()

    def save(self):
        with open(self.cache_file, 'w') as cache_f:
            json.dump(self.data, cache_f, sort_keys=False, indent=2)
        print(f"... cache at {self.cache_file} updated: now has {len(self.data)} entries")

def download_all(cache, source, results, dois):
    if (source["method"] == "get-html"):
        query = f'{source["site"]}?{"&".join(source["query"])}'
        html = cache.get(query)
        doc = BeautifulSoup(html, "html.parser")    
        totalStr = doc.select_one(source["count"]).text
        total = int(re.sub("\D", "", totalStr))
        pageSize = source["pageSize"]
        print(f'... query yields {total} matches, in batches of {pageSize}')
        for offset in range(0, total, pageSize):
            # query for entries in batch
            nquery = query.replace(
                source["pageExp"], 
                source["pageStart"] + str(offset//pageSize))
            html = cache.get(nquery)
            batch = extract_from_html(source, html, offset)   
            
            # try to retrieve metadata via doi for unknown dois in batch 
            missing_dois = []
            for r in batch:
                results.append(r)
                if (r.doi != '???' and not dois.has(r.doi)):
                    missing_dois.append(r.doi)
            
            if (len(missing_dois) > 0):
                new_dois = json.loads(cache.post(source['bibsite'], {
                    'dois': ",".join(missing_dois),
                    'targetFile': 'custom-bibtex',
                    'format': 'bibTex'
                }))
                for d in new_dois['items']:
                    for k, v in d.items():
                        dois.put(k, v)
    elif (source["method"] == "post-csv"):
        cache.post_json(
            source["initial"],
            source["query"]
        )
        csv_lines = cache.post_json(
            source["site"],
            source["query"]
        ).splitlines()
        print(f'... query yields {len(csv_lines)-1} matches')
        reader = csv.DictReader(csv_lines)
        i = 0
        selectors = source["columns"]
        for row in reader:
            be = BibEntry( 
                i,
                row[selectors['title']],
                row[selectors['authors']],
                row[selectors['date']],
                row[selectors['source']],
                row[selectors['doi']],
                source['name'],
                row[selectors['pubtype']],
                row[selectors['abstract']]
            )         
            i = i+1
            #print(f'{be}')
            results.append(be)
            if (r.doi != '???' and not dois.has(r.doi)):
                dois.put(r.doi, row)

    # save updated dois file
    dois.save()

    # patch abstracts back into results
    for r in results:
        if (dois.has(r.doi) and 'abstract' in dois.get(r.doi)): 
            r.abstract = dois.get(r.doi)['abstract']

def attempt_to_read(html, selector):
    try: 
        return html.select_one(selector).text
    except AttributeError:
        return "???"

def extract_from_html(source, html, offset):
    doc = BeautifulSoup(html, "html.parser")

    entries = doc.select("li.search__item")
    print(f'... found {len(entries)} matches at offset {offset}:');
    
    results = []
    selectors = source['selectors']
    for i, e in enumerate(entries):
        be = BibEntry( 
            i + offset,
            attempt_to_read(e, selectors['title']),
            attempt_to_read(e, selectors['authors']),
            attempt_to_read(e, selectors['date']),
            attempt_to_read(e, selectors['source']),
            attempt_to_read(e, selectors['doi'])
                .replace("https://doi.org/", ""),
            source['name'],
            '???', # pubtype
            '???' # abstract
        )         
        #print(f'{be}')
        results.append(be)
    return results

def main(source_file, output_file, cache_file, doi_file):  

    cache = ReqCache(cache_file)
    dois = DoiCache(doi_file)
    results = []

    with open(source_file, 'r') as source_f:
        sources = json.load(source_f)
        for source in sources:
            download_all(cache, source, results, dois)

    with open(output_file, 'w') as output_f:
        w = csv.writer(output_f, delimiter=' ', quotechar='|', quoting=csv.QUOTE_MINIMAL)
        for be in results:
            w.writerow(
                [be.doi] + [be.bibsource] + [be.idx] + [be.date] + 
                [be.title] + [be.authors] + [be.source] + [be.abstract])

if __name__ == '__main__':	
    parser = argparse.ArgumentParser(description=\
        "Download machine-readable bibliography from several databases")
    parser.add_argument("--source_file", 
            help="A file that describes a bibliography source")        
    parser.add_argument("--output_file", 
            help="Where to write the output; if exists, reprocess instead of download")
    parser.add_argument("--cache_file", 
            help="Where to cache requests; duplicate queries are read from the cache instead")         
    parser.add_argument("--doi_file", 
            help="Where to store DOI metadata. Data here is used to fill in any missing reference data.")                
    args = parser.parse_args()
    
    main(
        args.source_file, 
        args.output_file, 
        args.cache_file, 
        args.doi_file)
    