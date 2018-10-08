import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect

import ModSecurity

logger = logging.getLogger(__name__)

SETTINGS_NAMES = {
    'rule_files': 'MODSECURITY_RULE_FILES',
}

class PyModSecurityMiddleware(object):
    def __init__(self, get_response):
        '''
        PyModSecurityMiddleware
        This integrates the python bindings to the lib modsecurity to the
        django ecosystem

        :param callable get_response
        '''
        self.get_response = get_response

        self.modsecurity = ModSecurity.ModSecurity()
        self.rules = ModSecurity.Rules()

        self.rule_files = getattr(settings, SETTINGS_NAMES['rule_files'], None)
        if isinstance(self.rule_files, str):
            self.rule_files = [self.rule_files]

        self._rules_count = 0
        if self.rule_files is not None:
            self.load_rules(self.rule_files)

    @property
    def rules_count(self):
        return self._rules_count

    def load_rules(self, rule_files):
        '''
        Process a list of files (can be a list of globs) and loads into modsecurity
        :param list(str) rule_files
        '''
        import glob
        for pattern in rule_files:
            for rule_file in glob.glob(pattern, recursive=True):
                rules_count = self.rules.loadFromUri(rule_file)
                if rules_count < 0:
                    msg = '[ModSecurity] Error trying to load rule file %s. %s' % (rule_file, self.rules.getParserError())
                    print(msg)
                    logger.warning(msg)
                else:
                    self._rules_count += rules_count

    def __call__(self, request):
        transaction = ModSecurity.Transaction(self.modsecurity, self.rules)
        response = self.process_request(request, transaction)

        # We got an intervention response when processing the request
        # Do not proceed!
        if response is not None:
            return response

        response = self.get_response(request)
        response = self.process_response(request, response, transaction)
        return response

    def process_request(self, request, transaction):
        '''
        Process a request and checks with modsecurity if it's safe or if it should
        make an intervention
        '''
        meta = request.META
        transaction.processConnection(
            meta['REMOTE_ADDR'], int(request.get_port()),
            meta['SERVER_NAME'], int(meta['SERVER_PORT']))

        response = self.process_intervention(transaction)
        if response is not None:
            return response

        transaction.processURI(request.path, request.method, '1.1')
        response = self.process_intervention(transaction)
        if response is not None:
            return response

        for key, value in self._iter_headers(request):
            transaction.addRequestHeader(key, value)

        transaction.processRequestHeaders()
        response = self.process_intervention(transaction)
        if response is not None:
            return response

        transaction.appendRequestBody(request.body)
        transaction.processRequestBody()
        response = self.process_intervention(transaction)
        if response is not None:
            return response

        return None

    def _iter_headers(self, request):
        for key, value in request.META.items():
            if key.startswith('HTTP_'):
                yield key[5:], value

    def process_response(self, request, response, transaction):
        '''
        Process a response and checks with modsecurity if it's safe or if it should
        make an intervention
        '''
        intervention = self.process_intervention(transaction)

        # No intervention means safe response.
        # Just return what we've got
        if intervention is None:
            return response

        # ModSecurity needs to fix the response
        return HttpResponse()

    def process_intervention(self, transaction):
        '''
        Check if there's interventions
        :return the apropriate response, if any:
        :rtype HttpResponse:
        '''
        intervention = ModSecurity.ModSecurityIntervention()
        if transaction.intervention(intervention):
            if intervention is None:
                return None

            if not intervention.disruptive:
                return None

            # TODO process intervention logs

            if intervention.url is not None:
                response = HttpResponseRedirect(intervention.url)
            else:
                response = HttpResponse(status=intervention.status)

            return response
        else:
            return None