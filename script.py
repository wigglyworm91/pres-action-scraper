import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Self
import openai
import json
import markdownify
import glob
import os
import os.path
import io
from dataclasses_json import dataclass_json
from collections import defaultdict

try:
    import config
except ImportError:
    raise Exception('You must copy config_template.py to config.py and then fill it in before running this.')


POLL_TIME = 5 * 60 # seconds

ROOT_URL = 'https://www.whitehouse.gov/presidential-actions/'

OUTPUT_DIR = 'orders'

def curtime() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def slugify(url: str) -> str:
    '''
    >>> slugify('https://www.whitehouse.gov/presidential-actions/2025/04/addressing-risks-from-susman-godfrey/')
    '2025-04-addressing-risks-from-susman-godfrey'
    '''
    if url.endswith('/'):
        url = url[:-1]
    if url.startswith(ROOT_URL):
        return url[len(ROOT_URL):].replace('/', '-')

def tprint(s: str):
    print(f'[{curtime()}] {s}')

@dataclass_json
@dataclass
class ExecutiveOrder:
    title: Optional[str] = None
    url: Optional[str] = None
    scrape_date: Optional[datetime] = None
    date: Optional[datetime] = None
    text: Optional[str] = None
    summary: Optional[str] = None

    def load_text(self, force_reload: bool = False) -> None:
        if self.text is None or force_reload:
            r = requests.get(self.url)
            if r.status_code != 200:
                tprint(repr(r))
                raise Exception(f'Could not get text of executive order at {eo!r}')
            soup = BeautifulSoup(r.text, features='html.parser')
            self.text = markdownify.MarkdownConverter(strip=['h1']).convert_soup(soup.find('main'))
            page_title = soup.find('h1').text.strip()
            if page_title:
                if self.title != page_title:
                    tprint(f'Title changed? Previous title: "{self.title}", but new page title: "{page_title}"')
                self.title = page_title

    def generate_summary(self, force_reload: bool = False) -> None:
        if self.summary is None or force_reload:
            client = openai.OpenAI(
                api_key=config.OPENAI_API_KEY
            )
            completion = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[
                    {'role': 'developer', 'content': 'You are a helpful assistant.'},
                    {'role': 'user', 'content': f'{config.PREAMBLE} \n\n{self.text}'},
                ],
            )
            self.summary = completion.choices[0].message.content[:4000]

    def save_to_file(self, fpath: Optional[str] = None):
        data = self.to_json(indent=4)
        if fpath is None:
            fname = slugify(self.url)
            fpath = os.path.join(OUTPUT_DIR, f'{fname}.json')
            # if the caller specifies a path, we can overwrite; otherwise we don't want to do that
            i = 2
            while os.path.exists(fpath):
                fpath = os.path.join(OUTPUT_DIR, f'{fname}-{i}.json')
        with open(fpath, 'w') as f:
            f.write(data)
        tprint(f'Wrote {self} to {fpath}')

    @classmethod
    def load_from(self, fpath: str) -> Self:
        with open(fpath, 'r') as f:
            data = f.read()
        return ExecutiveOrder.from_json(data)

    def cast_to(self, hook: str):
        if self.text is None:
            raise Exception('text is None')
        if self.summary is None:
            raise Exception('summary is None')
        obj = {
            'content': None,
            'embed': {
                # filled in later
            },
            'fields': [
                {
                    'name': 'Link',
                    'value': self.url,
                },
                {
                    'name': 'Publish Date',
                    'value': self.date.strftime('%A, %B %-d, %Y'),
                }
            ],
        }
        if self.summary:
            obj['embed'] = {
                'title': f'{eo.title} (SUMMARY)',
                'description': self.summary[:4000],
            }
        else:
            obj['embed'] = {
                'title': f'{eo.title}',
                'description': self.text[:4000],
            }

        # include the text as an attachment
        files = {
            'text.md': io.StringIO(self.text)
        }

        r = requests.post(hook, data=json.dumps(obj), files=files)
        if r.status_code == 204:
            tprint(f'Discord notification successfully sent to {hook}')
        else:
            raise Exception(f'Failed to send notification to {hook}. Status code: {r.status_code}')

    def broadcast(self):
        for hook in WEBHOOK_URLS:
            self.cast_to(hook)


def get_current_eos() -> list[ExecutiveOrder]:
    r = requests.get(ROOT_URL)
    if r.status_code != 200:
        tprint(repr(r))
        raise Exception('Could not get presidential actions')
    soup = BeautifulSoup(r.text, 'html.parser')

    out = []
    h2_tags = soup.find_all('h2')
    for h2 in h2_tags:
        eo = ExecutiveOrder(
            title=h2.text.strip(),
        )
        if a := h2.find('a'):
            eo.url = a['href']
            if div2 := h2.find_next_sibling('div'):
                if time_tag := div2.find('time'):
                    eo.date = datetime.fromisoformat(time_tag['datetime'])
        eo.scrape_date = datetime.now()
        out.append(eo)
    return out

def load_eos_from_disk(direc: str = OUTPUT_DIR) -> list[ExecutiveOrder]:
    eos: list[ExecutiveOrder] = []
    for fname in glob.glob(f'{direc}/*.json'):
        try:
            eo = ExecutiveOrder.load_from(fname)
            eos.append(eo)
        except Exception as e:
            tprint(e)
    return eos

def load_cache_from_disk(direc: str = OUTPUT_DIR) -> dict[str, ExecutiveOrder]:
    eo_cache: dict[str, ExecutiveOrder] = {}
    for eo in load_eos_from_disk():
        if eo.url in eo_cache:
            tprint(f'Warning: duplicate EO detected: {eo.url} -- should be moved to duplicates folder')
        eo_cache[eo.url] = eo
    return eo_cache


if __name__ == '__main__':
    eo_cache = load_cache_from_disk()

    first_run = True
    while True:
        for eo in get_current_eos():
            if eo.url in eo_cache:
                # we've already seen this one
                tprint(f'Skipping {eo} - already seen and broadcast')
                continue
            eo_cache[eo.url] = eo

            # this is a new EO
            tprint(f'Found new EO: {eo.title}\n\t{eo.url}\n\t{eo.date}')
            try:
                eo.load_text()
            except Exception as e:
                tprint(f'Could not load text of EO {eo}: {e}')
                continue

            try:
                eo.generate_summary()
                pass
            except Exception as e:
                tprint(f'Could not generate summary of EO {eo}; issue with OpenAI? {e}')
                pass # we don't *need* a summary and openai might just be sad

            # no try/catch here - if we can't save to file something is deeply wrong
            eo.save_to_file()

            if first_run:
                # we don't actually want to broadcast these
                continue

            try:
                eo.broadcast()
            except Exception as e:
                tprint(f'Could not broadcast EO {eo}: {e}')
                continue

        first_run = False
        time.sleep(POLL_TIME)
