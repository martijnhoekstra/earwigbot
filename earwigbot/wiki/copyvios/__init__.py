# -*- coding: utf-8  -*-
#
# Copyright (C) 2009-2012 by Ben Kurtovic <ben.kurtovic@verizon.net>
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

from gzip import GzipFile
from StringIO import StringIO
from time import sleep, time
from urllib2 import build_opener, URLError

try:
    import oauth2 as oauth
except ImportError:
    oauth = None

from earwigbot import exceptions
from earwigbot.wiki.copyvios.markov import MarkovChain, MarkovChainIntersection
from earwigbot.wiki.copyvios.parsers import ArticleTextParser, HTMLTextParser
from earwigbot.wiki.copyvios.search import YahooBOSSSearchEngine

__all__ = ["CopyvioCheckResult", "CopyvioMixIn"]

class CopyvioCheckResult(object):
    """
    **EarwigBot: Wiki Toolset: Copyvio Check Result**

    A class holding information about the results of a copyvio check.

    *Attributes:*

    - :py:attr:`violation`:     ``True`` if this is a violation, else ``False``
    - :py:attr:`confidence`:    a float between 0 and 1 indicating accuracy
    - :py:attr:`url`:           the URL of the violated page
    - :py:attr:`queries`:       the number of queries used to reach a result
    - :py:attr:`article_chain`: the MarkovChain of the article text
    - :py:attr:`source_chain`:  the MarkovChain of the violated page text
    - :py:attr:`delta_chain`:   the MarkovChainIntersection comparing the two
    """

    def __init__(self, violation, confidence, url, queries, article, chains):
        self.violation = violation
        self.confidence = confidence
        self.url = url
        self.queries = queries
        self.article_chain = article
        self.source_chain = chains[0]
        self.delta_chain = chains[1]

    def __repr__(self):
        """Return the canonical string representation of the result."""
        res = "CopyvioCheckResult(violation={0!r}, confidence={1!r}, url={2!r}, queries={3|r})"
        return res.format(self.violation, self.confidence, self.url,
                          self.queries)

    def __str__(self):
        """Return a nice string representation of the result."""
        res = "<CopyvioCheckResult ({0} with {1} conf)>"
        return res.format(self.violation, self.confidence)


class CopyvioMixIn(object):
    """
    **EarwigBot: Wiki Toolset: Copyright Violation MixIn**

    This is a mixin that provides two public methods, :py:meth:`copyvio_check`
    and :py:meth:`copyvio_compare`. The former checks the page for copyright
    violations using a search engine API, and the latter compares the page
    against a given URL. Credentials for the search engine API are stored in
    the :py:class:`~earwigbot.wiki.site.Site`'s config.
    """

    def __init__(self, site):
        self._opener = build_opener()
        self._opener.addheaders = site._opener.addheaders

    def _open_url_ignoring_errors(self, url):
        """Open a URL using self._opener and return its content, or None.

        Will decompress the content if the headers contain "gzip" as its
        content encoding, and will return None if URLError is raised while
        opening the URL. IOErrors while gunzipping a compressed response are
        ignored, and the original content is returned.
        """
        try:
            response = self._opener.open(url)
        except URLError:
            return None
        result = response.read()

        if response.headers.get("Content-Encoding") == "gzip":
            stream = StringIO(result)
            gzipper = GzipFile(fileobj=stream)
            try:
                result = gzipper.read()
            except IOError:
                pass

        return result

    def _select_search_engine(self):
        """Return a function that can be called to do web searches.

        The function takes one argument, a search query, and returns a list of
        URLs, ranked by importance. The underlying logic depends on the
        *engine* argument within our config; for example, if *engine* is
        "Yahoo! BOSS", we'll use YahooBOSSSearchEngine for querying.

        Raises UnknownSearchEngineError if the 'engine' listed in our config is
        unknown to us, and UnsupportedSearchEngineError if we are missing a
        required package or module, like oauth2 for "Yahoo! BOSS".
        """
        engine, credentials = self._site._search_config

        if engine == "Yahoo! BOSS":
            if not oauth:
                e = "The package 'oauth2' could not be imported"
                raise exceptions.UnsupportedSearchEngineError(e)
            return YahooBOSSSearchEngine(credentials)

        raise exceptions.UnknownSearchEngineError(engine)

    def _copyvio_compare_content(self, article, url):
        """Return a number comparing an article and a URL.

        The *article* is a Markov chain, whereas the *url* is just a string
        that we'll try to open and read ourselves.
        """
        html = self._open_url_ignoring_errors(url)
        if not html:
            return 0

        source = MarkovChain(HTMLTextParser(html).strip())
        delta = MarkovChainIntersection(article, source)
        return float(delta.size()) / article.size(), (source, delta)

    def copyvio_check(self, min_confidence=0.5, max_queries=-1,
                      interquery_sleep=1):
        """Check the page for copyright violations.

        Returns a :py:class:`~earwigbot.wiki.copyvios.CopyvioCheckResult`
        object with information on the results of the check.

        *max_queries* is self-explanatory; we will never make more than this
        number of queries in a given check. If it's lower than 0, we will not
        limit the number of queries.

        *interquery_sleep* is the minimum amount of time we will sleep between
        search engine queries, in seconds.

        Raises :py:exc:`~earwigbot.exceptions.CopyvioCheckError` or subclasses
        (:py:exc:`~earwigbot.exceptions.UnknownSearchEngineError`,
        :py:exc:`~earwigbot.exceptions.SearchQueryError`, ...) on errors.
        """
        searcher = self._select_search_engine()
        handled_urls = []
        best_confidence = 0
        best_match = None
        num_queries = 0
        empty = MarkovChain("")
        best_chains = (empty, MarkovChainIntersection(empty, empty))
        parser = ArticleTextParser(self.get())
        clean = parser.strip()
        chunks = parser.chunk(max_queries)
        article_chain = MarkovChain(clean)
        last_query = time()

        if article_chain.size() < 20:  # Auto-fail very small articles
            return CopyvioCheckResult(False, best_confidence, best_match,
                                      num_queries, article_chain, best_chains)

        while (chunks and best_confidence < min_confidence and
               (max_queries < 0 or num_queries < max_queries)):
            urls = searcher.search(chunks.pop(0))
            urls = [url for url in urls if url not in handled_urls]
            for url in urls:
                handled_urls.append(url)
                conf, chains = self._copyvio_compare_content(article_chain, url)
                if conf > best_confidence:
                    best_confidence = conf
                    best_match = url
                    best_chains = chains
            num_queries += 1
            diff = time() - last_query
            if diff < interquery_sleep:
                sleep(interquery_sleep - diff)
            last_query = time()

        if best_confidence >= min_confidence:  # violation?
            v = True
        else:
            v = False
        return CopyvioCheckResult(v, best_confidence, best_match, num_queries,
                                  article_chain, best_chains)

    def copyvio_compare(self, url, min_confidence=0.5):
        """Check the page like :py:meth:`copyvio_check` against a specific URL.

        This is essentially a reduced version of the above - a copyivo
        comparison is made using Markov chains and the result is returned in a
        :py:class:`~earwigbot.wiki.copyvios.CopyvioCheckResult` object - but
        without using a search engine, since the suspected "violated" URL is
        supplied from the start.

        Its primary use is to generate a result when the URL is retrieved from
        a cache, like the one used in EarwigBot's Toolserver site. After a
        search is done, the resulting URL is stored in a cache for 24 hours so
        future checks against that page will not require another set of
        time-and-money-consuming search engine queries. However, the comparison
        itself (which includes the article's and the source's content) cannot
        be stored for data retention reasons, so a fresh comparison is made
        using this function.

        Since no searching is done, neither
        :py:exc:`~earwigbot.exceptions.UnknownSearchEngineError` nor
        :py:exc:`~earwigbot.exceptions.SearchQueryError` will be raised.
        """
        content = self.get()
        clean = ArticleTextParser(content).strip()
        article_chain = MarkovChain(clean)
        confidence, chains = self._copyvio_compare_content(article_chain, url)

        if confidence >= min_confidence:
            is_violation = True
        else:
            is_violation = False
        return CopyvioCheckResult(is_violation, confidence, url, 0,
                                  article_chain, chains)
