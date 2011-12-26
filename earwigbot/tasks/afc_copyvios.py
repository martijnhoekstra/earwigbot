# -*- coding: utf-8  -*-
#
# Copyright (C) 2009, 2010, 2011 by Ben Kurtovic <ben.kurtovic@verizon.net>
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is 
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from os.path import expanduser
from threading import Lock

import oursql

from earwigbot import wiki
from earwigbot.classes import BaseTask
from earwigbot.config import config

class Task(BaseTask):
    """A task to check newly-edited [[WP:AFC]] submissions for copyright
    violations."""
    name = "afc_copyvios"
    number = 1

    def __init__(self):
        config.decrypt(config.tasks, self.name, "search", "credentials", "key")
        config.decrypt(config.tasks, self.name, "search", "credentials", "secret")

        cfg = config.tasks.get(self.name, {})
        self.template = cfg.get("template", "AfC suspected copyvio")
        self.ignore_list = cfg.get("ignoreList", [])
        self.min_confidence = cfg.get("minConfidence", 0.75)
        self.max_queries = cfg.get("maxQueries", 10)
        default_summary = "Tagging suspected [[WP:COPYVIO|copyright violation]] of {url}"
        self.summary = self.make_summary(cfg.get("summary", default_summary))

        # Search API data:
        search = cfg.get("search", {})
        self.engine = search.get("engine")
        self.credentials = search.get("credentials", {})

        # Connection data for our SQL database:
        kwargs = cfg.get("sql", {})
        kwargs["read_default_file"] = expanduser("~/.my.cnf")
        self.conn_data = kwargs
        self.db_access_lock = Lock()

    def run(self, **kwargs):
        """Entry point for the bot task.

        Takes a page title in kwargs and checks it for copyvios, adding
        {{self.template}} at the top if a copyvio has been detected. A page is
        only checked once (processed pages are stored by page_id in an SQL
        database).
        """
        if self.shutoff_enabled():
            return
        title = kwargs["page"]
        page = wiki.get_site().get_page(title)
        with self.db_access_lock:
            self.conn = oursql.connect(**self.conn_data)
            self.process(page)

    def process(self, page):
        """Detect copyvios in 'page' and add a note if any are found."""
        title = page.title()
        if title in self.ignore_list:
            msg = "Skipping page in ignore list: [[{0}]]"
            self.logger.info(msg.format(title))
            return

        pageid = page.pageid()
        if self.has_been_processed(pageid):
            msg = "Skipping check on already processed page [[{0}]]"
            self.logger.info(msg.format(title))
            return

        self.logger.info("Checking [[{0}]]".format(title))
        result = page.copyvio_check(self.engine, self.credentials,
                                    self.min_confidence, self.max_queries)
        url = result.url
        confidence = "{0}%".format(round(result.confidence * 100, 2))

        if result.violation:
            content = page.get()
            template = "\{\{{0}|url={1}|confidence={2}\}\}"
            template = template.format(self.template, url, confidence)
            newtext = "\n".join((template, content))
            if "{url}" in self.summary:
                page.edit(newtext, self.summary.format(url=url))
            else:
                page.edit(newtext, self.summary)
            msg = "Found violation: [[{0}]] -> {1} ({2} confidence)"
            self.logger.warn(msg.format(title, url, confidence))
        else:
            msg = "No violations detected (best: {1} at {2} confidence)"
            self.logger.debug(msg.format(url, confidence))

        self.log_processed(pageid)

    def has_been_processed(self, pageid):
        """Returns True if pageid was processed before, otherwise False."""
        query = "SELECT 1 FROM processed WHERE page_id = ?"
        with self.conn.cursor() as cursor:
            cursor.execute(query, (pageid,))
            results = cursor.fetchall()
        if results:
            return True
        return False

    def log_processed(self, pageid):
        """Adds pageid to our database of processed pages.

        Raises an exception if the page has already been processed.
        """
        query = "INSERT INTO processed VALUES (?)"
        with self.conn.cursor() as cursor:
            cursor.execute(query, (pageid,))
