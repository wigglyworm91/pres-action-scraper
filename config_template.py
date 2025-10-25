# Copy this file to config.py and fill it in.

# OpenAI key - you need a paid account (at least $5) to get one of these. $5 gets you about 1000 summaries; they're very cheap.
# To make this key, go to https://platform.openai.com/settings/organization/api-keys
# If you don't want to use OpenAI, leave this blank (or add an invalid key) and the script will only scrape executive orders without summarizing them.
OPENAI_API_KEY = "sk-FILL_THIS_IN"



# Discord webhook URL to post updates
# Discord > Edit Channel > Integrations > Webhooks > New Webhook
WEBHOOK_URLS = []



# Preamble setting
PREAMBLE = "Please summarize the content of this recent executive order in under 4000 characters."
# Default preamble is fine, but this is an example of a custom prompt.
# You can modify this if you want to personalise your output for things that matter for you.
#PREAMBLE = "I am someone who lives in Oregon, works as a sanitation engineer for a private company, and travels internationally often. Please summarize the content of this recent executive order in under 4000 characters."


POLL_TIME = 5 * 60 # seconds

OUTPUT_DIR = 'orders'