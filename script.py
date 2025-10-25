import types
import typing
import requests
import argparse
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
import logging

class CustomFormatter(logging.Formatter):

    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    FORMATS = {
        logging.DEBUG: grey + format + reset,
        logging.INFO: format + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

logger = logging.getLogger('pres-action-scraper')
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
ch.setFormatter(CustomFormatter())
logger.addHandler(ch)

try:
    from config import POLL_TIME, PREAMBLE, WEBHOOK_URLS, OPENAI_API_KEY, OUTPUT_DIR
    assert isinstance(POLL_TIME, int)
    assert isinstance(PREAMBLE, str)
    assert isinstance(WEBHOOK_URLS, list)
    assert all(isinstance(url, str) for url in WEBHOOK_URLS)
    assert isinstance(OPENAI_API_KEY, str)
    assert isinstance(OUTPUT_DIR, str)
except ImportError:
    logger.error('You must copy config_template.py to config.py and then fill it in before running this.')
    raise
except AssertionError as e:
    logger.error('Your config.py file is malformed. Please check the types of the variables.')
    raise

if not os.path.exists(OUTPUT_DIR):
    logger.info(f'Creating output directory at {OUTPUT_DIR}')
    os.makedirs(OUTPUT_DIR)

if not WEBHOOK_URLS:
    logger.warning('No WEBHOOK_URLS specified in config.py; no broadcasts will be sent.')


ROOT_URL = 'https://www.whitehouse.gov/presidential-actions/'


def slugify(url: str) -> str:
    '''
    >>> slugify('https://www.whitehouse.gov/presidential-actions/2025/04/addressing-risks-from-susman-godfrey/')
    '2025-04-addressing-risks-from-susman-godfrey'
    '''
    if url.endswith('/'):
        url = url[:-1]
    if url.startswith(ROOT_URL):
        return url[len(ROOT_URL):].replace('/', '-')


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
            logger.info(f'Loading text for executive order at {self.url}')
            r = requests.get(self.url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, features='html.parser')
            self.text = markdownify.MarkdownConverter(strip=['h1']).convert_soup(soup.find('main'))
            page_title = soup.find('h1').text.strip()
            logger.info(f'Loaded text for executive order {page_title} at {self.url}')
            if page_title:
                if self.title != page_title:
                    logger.info(f'Title changed? Previous title: "{self.title}", but new page title: "{page_title}"')
                self.title = page_title

    def generate_summary(self, force_reload: bool = False) -> None:
        if self.summary is None or force_reload:
            client = openai.OpenAI(
                api_key=OPENAI_API_KEY
            )
            completion = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[
                    {'role': 'developer', 'content': 'You are a helpful assistant.'},
                    {'role': 'user', 'content': f'{PREAMBLE} \n\n{self.text}'},
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
                i += 1
        with open(fpath, 'w') as f:
            f.write(data)
        logger.info(f'Wrote {self.title} to {fpath}')

    @classmethod
    def load_from(self, fpath: str) -> Self:
        with open(fpath, 'r') as f:
            data = f.read()
        return ExecutiveOrder.from_json(data)

    def get_hook_data(self) -> tuple[dict, dict[str, typing.Any]]:
        if self.text is None and self.summary is None:
            raise Exception(f'text and summary are None for {self.title}')
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
                'description': self.text[:3997] + '...',
            }

        # include the text as an attachment
        files = {
            'text.md': io.StringIO(self.text)
        }
        return obj, files

    def broadcast(self, confirm=False) -> None:
        obj, files = self.get_hook_data()
        print(f'Broadcasting executive order "{self.title}" to {len(WEBHOOK_URLS)} webhook(s).')
        logger.info(f'Data to send to Discord webhook:\n{json.dumps(obj, indent=4)} with files {list(files.keys())}')
        for hook in WEBHOOK_URLS:
            if confirm:
                user_confirm = input(f'Send to webhook {hook}? (y/N): ')
                if user_confirm.lower() != 'y':
                    logger.info(f'Skipping webhook {hook} per user request')
                    continue
            r = requests.post(hook, data={'payload_json': json.dumps(obj)}, files=files)
            if r.status_code == 204:
                logger.debug(f'Discord notification successfully sent to {hook}')
            else:
                logger.error(f'Failed to send notification to {hook}. Status code: {r.status_code}')


def get_current_eos() -> list[ExecutiveOrder]:
    r = requests.get(ROOT_URL)
    if r.status_code != 200:
        logger.info(repr(r))
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
            logger.info(e)
    return eos

def load_cache_from_disk(direc: str = OUTPUT_DIR) -> dict[str, ExecutiveOrder]:
    eo_cache: dict[str, ExecutiveOrder] = {}
    for eo in load_eos_from_disk():
        if eo.url in eo_cache:
            logger.info(f'Warning: duplicate EO detected: {eo.url} -- should be moved to duplicates folder')
        eo_cache[eo.url] = eo
    return eo_cache


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Process executive orders')
    parser.add_argument('--once', action='store_true', help='Run once and exit, instead of polling continuously')
    parser.add_argument('--confirm', action='store_true', help='Ask for confirmation before broadcasting new executive orders')
    args = parser.parse_args()

    eo_cache = load_cache_from_disk()

    first_run = False
    while True:
        for eo in get_current_eos():
            if eo.url in eo_cache:
                # we've already seen this one
                logger.info(f'Skipping {eo.title} at {eo.url} - already seen')
                continue
            eo_cache[eo.url] = eo

            # this is a new EO
            logger.info(f'Found new EO: {eo.title}\n\t{eo.url}\n\t{eo.date}')
            try:
                eo.load_text()
            except Exception as e:
                logger.info(f'Could not load text of EO {eo.title}: {e}')
                continue

            try:
                eo.generate_summary()
                pass
            except Exception as e:
                logger.warning(f'Could not generate summary of EO {eo.title}; issue with OpenAI? {e}')
                pass # we don't *need* a summary and openai might just be sad

            # no try/catch here - if we can't save to file something is deeply wrong
            eo.save_to_file()

            if first_run:
                # we don't actually want to broadcast these
                continue

            eo.broadcast(confirm=args.confirm)
        
        if args.once:
            break

        first_run = False
        time.sleep(POLL_TIME)
