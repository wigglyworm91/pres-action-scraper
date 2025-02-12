import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
import openai
import json
import markdownify

try:
    import config
except ImportError:
    raise Exception('You must copy config_template.py to config.py and then fill it in before running this.')


POLL_TIME = 5 * 60

ROOT_URL = 'https://www.whitehouse.gov/presidential-actions/'

def curtime() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

@dataclass
class ExecutiveOrder:
    title: str
    url: Optional[str] = None
    date: Optional[datetime] = None

def get_current_eos() -> list[ExecutiveOrder]:
    r = requests.get(ROOT_URL)
    if r.status_code != 200:
        print(repr(r))
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
        out.append(eo)
    return out


def get_text_eo(eo: ExecutiveOrder) -> str:
    r = requests.get(eo.url)
    if r.status_code != 200:
        print(repr(r))
        raise Exception(f'Could not get text of EO: {eo!r}')
    soup = BeautifulSoup(r.text)
    return markdownify.MarkdownConverter(strip=['h1']).convert_soup(soup.find('main'))

def broadcast_new_eo(eo: ExecutiveOrder):
    # get the text
    text = get_text_eo(eo)

    # attempt to summarize the text
    print('attempting to summarize...')
    try:
        summary = summarize_with_openai(text)
    except Exception as e:
        print(e)
        summary = None

    # use webhook to do thing
    obj = {}
    obj['content'] = None
    embed = {}
    if summary:
        embed['title'] = f'{eo.title} (SUMMARY)'
        embed['description'] = summary[:4000]
    else:
        embed['title'] = eo.title
        embed['description'] = text[:4000]
    embed['fields'] = [
        {
            'name': 'Link',
            'value': eo.url
        },
        {
            'name': 'Publish Date',
            'value': eo.date.strftime('%A, %B %-d, %Y')
        }
    ]
    obj['embeds'] = [embed]
    obj['attachments'] = []

    for webhook_url in config.WEBHOOK_URLS:
        # try to send webhook
        r = requests.post(webhook_url, data=json.dumps(obj), headers={'Content-Type': 'application/json'})
        if r.status_code == 204:
            print(f'discord noti sent successfully to {webhook_url}')
        else:
            print(f'Failed to send message. Status code: {r.status_code}')

def summarize_with_openai(text: str) -> str:
    client = openai.OpenAI(
        api_key=config.OPENAI_API_KEY
    )

    completion = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[
            {'role': 'developer', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': f'{config.PREAMBLE} \n\n{text}'},
        ],
    )

    return completion.choices[0].message.content[:4000]


prev_seen_urls = set()
first_run = True
while True:
    eos = get_current_eos()

    for url in prev_seen_urls - set(eo.url for eo in eos):
        # almost certainly simply dropped off the front page
        print(f'[{curtime()}] EO dropped off: {url}')

    for eo in eos:
        if eo.url not in prev_seen_urls:
            print(f'[{curtime()}] Found new EO: {eo.title}\n\t{eo.url}\n\t{eo.date}')
            prev_seen_urls.add(eo.url)
            if not first_run:
                try:
                    broadcast_new_eo(eo)
                except Exception as e:
                    print(f'[{curtime()}] Exception broadcasting EO: {e!r}')

    first_run = False
    time.sleep(5 * 60)
