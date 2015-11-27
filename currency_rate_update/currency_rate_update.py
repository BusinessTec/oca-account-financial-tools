# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (c) 2009 Camptocamp SA
#    @source JBA and AWST inpiration
#    @contributor Grzegorz Grzelak (grzegorz.grzelak@birdglobe.com),
#                 Joel Grand-Guillaume
#    Copyright (c) 2010 Alexis de Lattre (alexis@via.ecp.fr)
#     - ported XML-based webservices (Admin.ch, ECB, PL NBP) to new XML lib
#     - rates given by ECB webservice is now correct even when main_cur <> EUR
#     - rates given by PL_NBP webs. is now correct even when main_cur <> PLN
#     - if company_currency <> CHF, you can now update CHF via Admin.ch
#       (same for EUR with ECB webservice and PLN with NBP webservice)
#     For more details, see Launchpad bug #645263
#     - mecanism to check if rates given by the webservice are "fresh"
#       enough to be written in OpenERP
#       ('max_delta_days' parameter for each currency update service)
#    Ported to OpenERP 7.0 by Lorenzo Battistini
#                             <lorenzo.battistini@agilebg.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

# TODO "nice to have" : restrain the list of currencies that can be added for
# a webservice to the list of currencies supported by the Webservice
# TODO : implement max_delta_days for Yahoo webservice

import logging
import time
from datetime import datetime, timedelta
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT
from openerp.osv import fields, osv, orm
from openerp import pooler 
from openerp.tools.translate import _
from openerp.exceptions import Warning

class Currency_rate_update(osv.Model):
    """Class that handle an ir cron call who will
    update currencies based on a web url"""
    _inherit = 'res.currency' 
    
    logger2 = logging.getLogger('currency.rate.update')
    #logger = netsvc.().#logger = logging.getLogger('_name_')#netsvc.Logger()
    #LOG_NAME = 'cron-rates'
    #MOD_NAME = 'c2c_currency_rate_update: '
    
    _description = "Currency Rate Update"
    _columns={
              
         'webservice': fields.selection(
            [
                ('Admin_ch_getter', 'Admin.ch'),
                ('ECB_getter', 'European Central Bank'),
                ('Yahoo_getter', 'Yahoo Finance '),
                # Added for polish rates
                ('PL_NBP_getter', 'Narodowy Bank Polski'),
                # Added for mexican rates
                ('Banxico_getter', 'Banco de México'),
                # Bank of Canada is using RSS-CB
                # http://www.cbwiki.net/wiki/index.php/Specification_1.1
                # This RSS format is used by other national banks
                #  (Thailand, Malaysia, Mexico...)
                ('CA_BOC_getter', 'Bank of Canada - noon rates'),
                ('bccr_getter', 'Banco Central de Costa Rica'),  # Added for CR rates
            ],
            "Webservice to use",
        ),
        'code_rate': fields.char('Code rate', size=64), # Just for Costa Rica web service
        'ir_cron_job_id': fields.many2one('ir.cron', 'Automatic Update Task',), 
        'automatic_update': fields.boolean('Automatic Update'),
        'interval_number': fields.related('ir_cron_job_id', 'interval_number', type='integer', string='Interval Number',help="Repeat every x."), 
        'nextcall' : fields.related('ir_cron_job_id', 'nextcall', type='datetime', string='Next Execution Date', help="Next planned execution date for this job."),
        'doall' : fields.related('ir_cron_job_id', 'doall', type='boolean', string='Repeat Missed', help="Specify if missed occurrences should be executed when the server restarts."),
        'interval_type': fields.related('ir_cron_job_id', 'interval_type', type='selection', selection=[('minutes', 'Minutes'), ('hours', 'Hours'), ('work_days','Work Days'), ('days', 'Days'),('weeks', 'Weeks'), ('months', 'Months')], string='Interval Unit'),
        'numbercall': fields.related('ir_cron_job_id', 'numbercall', type='integer', string='Number of Calls', help='How many times the method is called,\na negative number indicates no limit.'),
           
              }
    
    _defaults = {
        'interval_type' : 'days',
    }
    
    #===========================================================================
    # Create a generic method for cron_job creation
    # It could be call from create or write. Create dictionary with 
    # cron_job values
    #===========================================================================
    def cron_job_creation(self, cr, uid, ids=[], vals={}, mode='', context=None):
        res = {}
        #If method is called from write
        if mode == 'write':
            #For write, vals dictionary only have new values (or values with some changes)         
            currency_obj = self.browse(cr, uid, ids, context=context)[0] #Find currency that already exists in database
            name = "Exchanges Rate Cron for currency " + currency_obj.name
           
        elif mode == 'create':
            name = "Exchanges Rate Cron for currency " + vals['name']
        
        #Cron job name. "Clean" name for unnecessary characters. Avoid create
        #name as a tuple.
        name.replace(')','')
        res.update({'name': name})
        
        res.update ({
               'interval_type': 'days',
               'nextcall': time.strftime("%Y-%m-%d %H:%M:%S", (datetime.today() + timedelta(days=1)).timetuple() ), #tomorrow same time
               'interval_number': 1,
               'numbercall': -1,
               'doall': True,
               'model': 'res.currency', 
               'function': 'run_currency_update',                                              
               'active':True,
               })
        
        return res
    
    #===========================================================================
    """
        @param context: If 'from_ir_cron' key exists, it means that context comes
                        from ir_cron form and write only needs to update state for
                        automatic_update field.
                        
                        If this key doesn't exist in context, keep with the other
                        process.
    """
    def write(self, cr, uid, ids, vals, context=None):
        #For write, vals dictionary only have new values (or values with some changes)
        for currency_obj in self.browse(cr, uid, ids, context=context): #Find currency that already exists in database            
            if 'from_ir_cron' not in context.keys():
                if 'automatic_update' in vals.keys():
                    if vals['automatic_update']: #Check as True
                        if not currency_obj.ir_cron_job_id: #if currency doesn't have a cron associated
                            res = self.cron_job_creation(cr, uid, ids=[currency_obj.id], vals=vals, mode='write', context=context)
                            #include currency_id -> used later for update currency
                            res.update({'args':[currency_obj.id]})
                            #create cron_job
                            cron_job_id = self.pool.get('ir.cron').create(cr, uid, res, context=context)
                            #update currency
                            vals.update({'ir_cron_job_id': cron_job_id})
                        
                        else:
                            update = {}
                            cron_job_id = currency_obj.ir_cron_job_id.id
                            #Extract only values with changes. Update cron job related to currency
                            for key, val in vals.iteritems():
                                #automatic_update is from currency, it won't be included
                                #in values to update from ir.cron.
                                #associate key 'active' in ir.cron object with value in
                                #automatic_update
                                if key not in update.keys() and key != 'automatic_update': 
                                    update[key] = vals[key]                    
                            update.update({'active':vals['automatic_update']})
                            self.pool.get('ir.cron').write(cr, uid, [cron_job_id], update, context=context)
                    #Don't unlink cron_job. It will pass to inactive state
                    elif currency_obj.ir_cron_job_id:
                        update_cron = {'active': False}
                        self.pool.get('ir.cron').write(cr, uid, [currency_obj.ir_cron_job_id.id], update_cron, context=context)
        return super(Currency_rate_update, self).write(cr, uid, ids, vals, context=context)

    def create(self, cr, uid, vals, context={}):  
        cron_job = {}
        #First create currency
        res = super(Currency_rate_update, self).create(cr, uid, vals, context=context)
        
        #Create cron_job
        if 'automatic_update' in vals.keys():
            cron_job = self.cron_job_creation(cr, uid, ids=[], vals=vals, mode='create', context=context)
            #include currency_id in cron_job
            cron_job.update({'args': [int(res)]})
            #create cron_job
            cron_job_id = self.pool.get('ir.cron').create(cr, uid, cron_job, context=context)
            vals.update({'ir_cron_job_id': cron_job_id})
            self.write(cr, uid, ids, vals, context=context)
        return res

    def run_currency_update(self, cr, uid, arg1=None): 
        
        curr_obj = self.pool.get('res.currency')
        rate_obj = self.pool.get('res.currency.rate')
        
        #=======Currency to update
        #Find currency 
        currency_id = curr_obj.browse(cr, uid,[arg1],context=None)[0]
        #Find service associated to currency
        service = currency_id.webservice
                  
            #========Base currency
        res_currency_base_id = curr_obj.search(cr, uid, [('base', '=', True)])
        if not res_currency_base_id:
            raise Warning(_('There is no base currency set'))
        res_currency_base = curr_obj.browse(cr, uid, res_currency_base_id)[0]
            
        factory = Currency_getter_factory()
        new_rate_ids = []
        
        try:
                 #Initialize service class
            getter = factory.register(service)
                 #get_update_currency return a dictionary with rate and name's currency 
                 #receive a array with currency to update
            res, log_info = getter.get_updated_currency(cr, uid, [currency_id.name],res_currency_base.name)
            #In res_currency_service, name is date when the rate is updated
            for date, rate in res[currency_id.name].iteritems():
                rate_ids = rate_obj.search(cr, uid, [('currency_id','=',currency_id.id),('name','=',date)])
                rate = float(rate)
                if currency_id.sequence:
                    rate = 1.0/rate
                    vals = {'currency_id': currency_id.id, 'rate': rate, 'name': date}
                else:
                    vals = {'currency_id': currency_id.id, 'rate': rate, 'name': date}
                if not len(rate_ids):
                    new_rate_ids.append(rate_obj.create(cr, uid, vals))
                else:
                    rate_obj.write(cr,uid, rate_ids, vals, context=None)
                    new_rate_ids += rate_ids
            self.logger2.info('Update finished...')
            return new_rate_ids
        except Exception as e:
            self.logger2.info("Unable to update %s, %s" % (currency_id.name, str(e)))

def get_cron_id(self, cr, uid, context):
        """Returns the updater cron's id.
        Create one if the cron does not exists
        """

        cron_id = 0
        cron_obj = self.pool.get('ir.cron')
        try:
            # Finds the cron that send messages
            cron_id = cron_obj.search(
                cr,
                uid,
                [
                    ('function', 'ilike', self.cron['function']),
                    ('model', 'ilike', self.cron['model'])
                ],
                context={
                    'active_test': False
                }
            )
            cron_id = int(cron_id[0])
        except Exception:
            _logger.info('warning cron not found one will be created')
            # Ignore if the cron is missing cause we are
            # going to create it in db
            pass
        if not cron_id:
            self.cron['name'] = _('Currency Rate Update')
            cron_id = cron_obj.create(cr, uid, self.cron, context)
        return cron_id

def save_cron(self, cr, uid, datas, context={}):
        """save the cron config data should be a dict"""
        cron_id = self.get_cron_id(cr, uid, context)
        return self.pool.get('ir.cron').write(cr, uid, [cron_id], datas)

class AbstractClassError(Exception):
    def __str__(self):
        return 'Abstract Class'

    def __repr__(self):
        return 'Abstract Class'


class AbstractMethodError(Exception):
    def __str__(self):
        return 'Abstract Method'

    def __repr__(self):
        return 'Abstract Method'


class UnknowClassError(Exception):
    def __str__(self):
        return 'Unknown Class'

    def __repr__(self):
        return 'Unknown Class'


class UnsuportedCurrencyError(Exception):
    def __init__(self, value):
        self.curr = value

    def __str__(self):
        return 'Unsupported currency %s' % self.curr

    def __repr__(self):
        return 'Unsupported currency %s' % self.curr


class Currency_getter_factory():
    """Factory pattern class that will return
    a currency getter class base on the name passed
    to the register method

    """
    def register(self, class_name):
        allowed = [
            'Admin_ch_getter',
            'PL_NBP_getter',
            'ECB_getter',
            'NYFB_getter',
            'Google_getter',
            'Yahoo_getter',
            'Banxico_getter',
            'CA_BOC_getter',
            'bccr_getter',
        ]
        if class_name in allowed:
            class_def = eval(class_name)
            return class_def()
        else:
            raise UnknowClassError


class Curreny_getter_interface(object):
    "Abstract class of currency getter"

    log_info = " "

    supported_currency_array = [
        'AED', 'AFN', 'ALL', 'AMD', 'ANG', 'AOA', 'ARS', 'AUD', 'AWG', 'AZN',
        'BAM', 'BBD', 'BDT', 'BGN', 'BHD', 'BIF', 'BMD', 'BND', 'BOB', 'BRL',
        'BSD', 'BTN', 'BWP', 'BYR', 'BZD', 'CAD', 'CDF', 'CHF', 'CLP', 'CNY',
        'COP', 'CRC', 'CUP', 'CVE', 'CYP', 'CZK', 'DJF', 'DKK', 'DOP', 'DZD',
        'EEK', 'EGP', 'ERN', 'ETB', 'EUR', 'FJD', 'FKP', 'GBP', 'GEL', 'GGP',
        'GHS', 'GIP', 'GMD', 'GNF', 'GTQ', 'GYD', 'HKD', 'HNL', 'HRK', 'HTG',
        'HUF', 'IDR', 'ILS', 'IMP', 'INR', 'IQD', 'IRR', 'ISK', 'JEP', 'JMD',
        'JOD', 'JPY', 'KES', 'KGS', 'KHR', 'KMF', 'KPW', 'KRW', 'KWD', 'KYD',
        'KZT', 'LAK', 'LBP', 'LKR', 'LRD', 'LSL', 'LTL', 'LVL', 'LYD', 'MAD',
        'MDL', 'MGA', 'MKD', 'MMK', 'MNT', 'MOP', 'MRO', 'MTL', 'MUR', 'MVR',
        'MWK', 'MXN', 'MYR', 'MZN', 'NAD', 'NGN', 'NIO', 'NOK', 'NPR', 'NZD',
        'OMR', 'PAB', 'PEN', 'PGK', 'PHP', 'PKR', 'PLN', 'PYG', 'QAR', 'RON',
        'RSD', 'RUB', 'RWF', 'SAR', 'SBD', 'SCR', 'SDG', 'SEK', 'SGD', 'SHP',
        'SLL', 'SOS', 'SPL', 'SRD', 'STD', 'SVC', 'SYP', 'SZL', 'THB', 'TJS',
        'TMM', 'TND', 'TOP', 'TRY', 'TTD', 'TVD', 'TWD', 'TZS', 'UAH', 'UGX',
        'USD', 'UYU', 'UZS', 'VEB', 'VEF', 'VND', 'VUV', 'WST', 'XAF', 'XAG',
        'XAU', 'XCD', 'XDR', 'XOF', 'XPD', 'XPF', 'XPT', 'YER', 'ZAR', 'ZMK',
        'ZWD'
    ]

    # Updated currency this arry will contain the final result
    updated_currency = {}

    def get_updated_currency(self, currency_array, main_currency,
                             max_delta_days):
        """Interface method that will retrieve the currency
           This function has to be reinplemented in child
        """
        raise AbstractMethodError

    def validate_cur(self, currency):
        """Validate if the currency to update is supported"""
        if currency not in self.supported_currency_array:
            raise UnsuportedCurrencyError(currency)

    def get_url(self, url):
        """Return a string of a get url query"""
        try:
            import urllib
            objfile = urllib.urlopen(url)
            rawfile = objfile.read()
            objfile.close()
            return rawfile
        except ImportError:
            raise osv.except_osv(
                'Error !',
                self.MOD_NAME + 'Unable to import urllib !'
            )
        except IOError:
            raise osv.except_osv(
                'Error !',
                self.MOD_NAME + 'Web Service does not exist !'
            )

    def check_rate_date(self, rate_date, max_delta_days):
        """Check date constrains. rate_date must be of datetime type"""
        days_delta = (datetime.today() - rate_date).days
        if days_delta > max_delta_days:
            raise Exception(
                'The rate timestamp (%s) is %d days away from today, '
                'which is over the limit (%d days). '
                'Rate not updated in OpenERP.' % (rate_date,
                                                  days_delta,
                                                  max_delta_days)
            )

        # We always have a warning when rate_date != today
        rate_date_str = datetime.strftime(rate_date,
                                          DEFAULT_SERVER_DATE_FORMAT)
        if rate_date.date() != datetime.today().date():
            msg = "The rate timestamp (%s) is not today's date"
            self.log_info = ("WARNING : %s %s") % (msg, rate_date_str)
            _logger.warning(msg, rate_date_str)


# Yahoo #######################################################################
class Yahoo_getter(Curreny_getter_interface):
    """Implementation of Currency_getter_factory interface
    for Yahoo finance service
    """

    def get_updated_currency(self, cr, uid, currency_array, main_currency):
        """implementation of abstract method of curreny_getter_interface"""
        self.validate_cur(main_currency)
        url = ('http://download.finance.yahoo.com/d/'
               'quotes.txt?s="%s"=X&f=sl1c1abg')
        if main_currency in currency_array:
            currency_array.remove(main_currency)
        for curr in currency_array:
            self.validate_cur(curr)
            res = self.get_url(url % (main_currency + curr))
            val = res.split(',')[1]
            if val:
                self.updated_currency[curr] = val
            else:
                raise Exception('Could not update the %s' % (curr))
        return self.updated_currency, self.log_info

# Admin CH ####################################################################
class Admin_ch_getter(Curreny_getter_interface):
    """Implementation of Currency_getter_factory interface
    for Admin.ch service

    """

    def rate_retrieve(self, dom, ns, curr):
        """Parse a dom node to retrieve currencies data"""
        res = {}
        xpath_rate_currency = ("/def:wechselkurse/def:devise[@code='%s']/"
                               "def:kurs/text()") % (curr.lower())
        xpath_rate_ref = ("/def:wechselkurse/def:devise[@code='%s']/"
                          "def:waehrung/text()") % (curr.lower())
        res['rate_currency'] = float(
            dom.xpath(xpath_rate_currency, namespaces=ns)[0]
        )
        res['rate_ref'] = float(
            (dom.xpath(xpath_rate_ref, namespaces=ns)[0]).split(' ')[0]
        )
        return res

    def get_updated_currency(self, cr, uid, currency_array, main_currency):
        """Implementation of abstract method of Curreny_getter_interface"""
        url = ('http://www.afd.admin.ch/publicdb/newdb/'
               'mwst_kurse/wechselkurse.php')
        # We do not want to update the main currency
        if main_currency in currency_array:
            currency_array.remove(main_currency)
        # Move to new XML lib cf Launchpad bug #645263
        from lxml import etree
        _logger.debug("Admin.ch currency rate service : connecting...")
        rawfile = self.get_url(url)
        dom = etree.fromstring(rawfile)
        _logger.debug("Admin.ch sent a valid XML file")
        adminch_ns = {
            'def': 'http://www.afd.admin.ch/publicdb/newdb/mwst_kurse'
        }
        rate_date = dom.xpath(
            '/def:wechselkurse/def:datum/text()',
            namespaces=adminch_ns
        )
        rate_date = rate_date[0]
        rate_date_datetime = datetime.strptime(rate_date,
                                               DEFAULT_SERVER_DATE_FORMAT)
        self.check_rate_date(rate_date_datetime, max_delta_days)
        # we dynamically update supported currencies
        self.supported_currency_array = dom.xpath(
            "/def:wechselkurse/def:devise/@code",
            namespaces=adminch_ns
        )
        self.supported_currency_array = [x.upper() for x
                                         in self.supported_currency_array]
        self.supported_currency_array.append('CHF')

        _logger.debug(
            "Supported currencies = " + str(self.supported_currency_array)
        )
        self.validate_cur(main_currency)
        if main_currency != 'CHF':
            main_curr_data = self.rate_retrieve(dom, adminch_ns, main_currency)
            # 1 MAIN_CURRENCY = main_rate CHF
            rate_curr = main_curr_data['rate_currency']
            rate_ref = main_curr_data['rate_ref']
            main_rate = rate_curr / rate_ref
        for curr in currency_array:
            self.validate_cur(curr)
            if curr == 'CHF':
                rate = main_rate
            else:
                curr_data = self.rate_retrieve(dom, adminch_ns, curr)
                # 1 MAIN_CURRENCY = rate CURR
                if main_currency == 'CHF':
                    rate = curr_data['rate_ref'] / curr_data['rate_currency']
                else:
                    rate = (main_rate * curr_data['rate_ref'] /
                            curr_data['rate_currency'])
            self.updated_currency[curr] = rate
            _logger.debug(
                "Rate retrieved : 1 %s = %s %s" % (main_currency, rate, curr)
            )
        return self.updated_currency, self.log_info


# ECB getter #################################################################
class ECB_getter(Curreny_getter_interface):
    """Implementation of Currency_getter_factory interface
    for ECB service
    """

    def rate_retrieve(self, dom, ns, curr):
        """Parse a dom node to retrieve-
        currencies data

        """
        res = {}
        xpath_curr_rate = ("/gesmes:Envelope/def:Cube/def:Cube/"
                           "def:Cube[@currency='%s']/@rate") % (curr.upper())
        res['rate_currency'] = float(
            dom.xpath(xpath_curr_rate, namespaces=ns)[0]
        )
        return res

    def get_updated_currency(self, cr, uid, currency_array, main_currency):
        """implementation of abstract method of Curreny_getter_interface"""
        url = 'http://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
        # Important : as explained on the ECB web site, the currencies are
        # at the beginning of the afternoon ; so, until 3 p.m. Paris time
        # the currency rates are the ones of trading day N-1
        # http://www.ecb.europa.eu/stats/exchange/eurofxref/html/index.en.html

        # We do not want to update the main currency
        if main_currency in currency_array:
            currency_array.remove(main_currency)
        # Move to new XML lib cf Launchpad bug #645263
        from lxml import etree
        _logger.debug("ECB currency rate service : connecting...")
        rawfile = self.get_url(url)
        dom = etree.fromstring(rawfile)
        _logger.debug("ECB sent a valid XML file")
        ecb_ns = {
            'gesmes': 'http://www.gesmes.org/xml/2002-08-01',
            'def': 'http://www.ecb.int/vocabulary/2002-08-01/eurofxref'
        }
        rate_date = dom.xpath('/gesmes:Envelope/def:Cube/def:Cube/@time',
                              namespaces=ecb_ns)[0]
        rate_date_datetime = datetime.strptime(rate_date,
                                               DEFAULT_SERVER_DATE_FORMAT)
        self.check_rate_date(rate_date_datetime, max_delta_days)
        # We dynamically update supported currencies
        self.supported_currency_array = dom.xpath(
            "/gesmes:Envelope/def:Cube/def:Cube/def:Cube/@currency",
            namespaces=ecb_ns
        )
        self.supported_currency_array.append('EUR')
        _logger.debug("Supported currencies = %s " %
                      self.supported_currency_array)
        self.validate_cur(main_currency)
        if main_currency != 'EUR':
            main_curr_data = self.rate_retrieve(dom, ecb_ns, main_currency)
        for curr in currency_array:
            self.validate_cur(curr)
            if curr == 'EUR':
                rate = 1 / main_curr_data['rate_currency']
            else:
                curr_data = self.rate_retrieve(dom, ecb_ns, curr)
                if main_currency == 'EUR':
                    rate = curr_data['rate_currency']
                else:
                    rate = (curr_data['rate_currency'] /
                            main_curr_data['rate_currency'])
            self.updated_currency[curr] = rate
            _logger.debug(
                "Rate retrieved : 1 %s = %s %s" % (main_currency, rate, curr)
            )
        return self.updated_currency, self.log_info


# PL NBP ######################################################################
class PL_NBP_getter(Curreny_getter_interface):
    """Implementation of Currency_getter_factory interface
    for PL NBP service

    """

    def rate_retrieve(self, dom, ns, curr):
        """ Parse a dom node to retrieve
        currencies data"""
        res = {}
        xpath_rate_currency = ("/tabela_kursow/pozycja[kod_waluty='%s']/"
                               "kurs_sredni/text()") % (curr.upper())
        xpath_rate_ref = ("/tabela_kursow/pozycja[kod_waluty='%s']/"
                          "przelicznik/text()") % (curr.upper())
        res['rate_currency'] = float(
            dom.xpath(xpath_rate_currency, namespaces=ns)[0].replace(',', '.')
        )
        res['rate_ref'] = float(dom.xpath(xpath_rate_ref, namespaces=ns)[0])
        return res

    def get_updated_currency(self, cr, uid, currency_array, main_currency):
        """implementation of abstract method of Curreny_getter_interface"""
        # LastA.xml is always the most recent one
        url = 'http://www.nbp.pl/kursy/xml/LastA.xml'
        # We do not want to update the main currency
        if main_currency in currency_array:
            currency_array.remove(main_currency)
        # Move to new XML lib cf Launchpad bug #645263
        from lxml import etree
        _logger.debug("NBP.pl currency rate service : connecting...")
        rawfile = self.get_url(url)
        dom = etree.fromstring(rawfile)
        ns = {}  # Cool, there are no namespaces !
        _logger.debug("NBP.pl sent a valid XML file")
        rate_date = dom.xpath('/tabela_kursow/data_publikacji/text()',
                              namespaces=ns)[0]
        rate_date_datetime = datetime.strptime(rate_date,
                                               DEFAULT_SERVER_DATE_FORMAT)
        self.check_rate_date(rate_date_datetime, max_delta_days)
        # We dynamically update supported currencies
        self.supported_currency_array = dom.xpath(
            '/tabela_kursow/pozycja/kod_waluty/text()',
            namespaces=ns
        )
        self.supported_currency_array.append('PLN')
        _logger.debug("Supported currencies = %s" %
                      self.supported_currency_array)
        self.validate_cur(main_currency)
        if main_currency != 'PLN':
            main_curr_data = self.rate_retrieve(dom, ns, main_currency)
            # 1 MAIN_CURRENCY = main_rate PLN
            main_rate = (main_curr_data['rate_currency'] /
                         main_curr_data['rate_ref'])
        for curr in currency_array:
            self.validate_cur(curr)
            if curr == 'PLN':
                rate = main_rate
            else:
                curr_data = self.rate_retrieve(dom, ns, curr)
                # 1 MAIN_CURRENCY = rate CURR
                if main_currency == 'PLN':
                    rate = curr_data['rate_ref'] / curr_data['rate_currency']
                else:
                    rate = (main_rate * curr_data['rate_ref'] /
                            curr_data['rate_currency'])
            self.updated_currency[curr] = rate
            _logger.debug("Rate retrieved : %s = %s %s" %
                          (main_currency, rate, curr))
        return self.updated_currency, self.log_info


# Banco de México #############################################################
class Banxico_getter(Curreny_getter_interface):
    """Implementation of Currency_getter_factory interface
    for Banco de México service

    """

    def rate_retrieve(self):
        """ Get currency exchange from Banxico.xml and proccess it
        TODO: Get correct data from xml instead of process string
        """
        url = ('http://www.banxico.org.mx/rsscb/rss?'
               'BMXC_canal=pagos&BMXC_idioma=es')

        from xml.dom.minidom import parse
        from StringIO import StringIO

        logger = logging.getLogger(__name__)
        logger.debug("Banxico currency rate service : connecting...")
        rawfile = self.get_url(url)

        dom = parse(StringIO(rawfile))
        logger.debug("Banxico sent a valid XML file")

        value = dom.getElementsByTagName('cb:value')[0]
        rate = value.firstChild.nodeValue

        return float(rate)

    def get_updated_currency(self, cr, uid, currency_array, main_currency):
        """implementation of abstract method of Curreny_getter_interface"""
        logger = logging.getLogger(__name__)
        # we do not want to update the main currency
        if main_currency in currency_array:
            currency_array.remove(main_currency)

        # Suported currencies
        suported = ['MXN', 'USD']
        for curr in currency_array:
            if curr in suported:
                # Get currency data
                main_rate = self.rate_retrieve()
                if main_currency == 'MXN':
                    rate = 1 / main_rate
                else:
                    rate = main_rate
            else:
                # No other currency supported
                continue

            self.updated_currency[curr] = rate
            logger.debug("Rate retrieved : %s = %s %s" %
                         (main_currency, rate, curr))


# CA BOC #####   Bank of Canada   #############################################
class CA_BOC_getter(Curreny_getter_interface):
    """Implementation of Curreny_getter_factory interface
    for Bank of Canada RSS service

    """

    def get_updated_currency(self, cr, uid, currency_array, main_currency):
        """implementation of abstract method of Curreny_getter_interface"""

        # as of Jan 2014 BOC is publishing noon rates for about 60 currencies
        url = ('http://www.bankofcanada.ca/stats/assets/'
               'rates_rss/noon/en_%s.xml')
        # closing rates are available as well (please note there are only 12
        # currencies reported):
        # http://www.bankofcanada.ca/stats/assets/rates_rss/closing/en_%s.xml

        # We do not want to update the main currency
        if main_currency in currency_array:
            currency_array.remove(main_currency)

        import feedparser
        import pytz
        from dateutil import parser

        for curr in currency_array:

            _logger.debug("BOC currency rate service : connecting...")
            dom = feedparser.parse(url % curr)

            self.validate_cur(curr)

            # check if BOC service is running
            if dom.bozo and dom.status != 404:
                _logger.error("Bank of Canada - service is down - try again\
                    later...")

            # check if BOC sent a valid response for this currency
            if dom.status != 200:
                _logger.error("Exchange data for %s is not reported by Bank\
                    of Canada." % curr)
                raise osv.except_osv('Error !', 'Exchange data for %s is not\
                    reported by Bank of Canada.' % str(curr))

            _logger.debug("BOC sent a valid RSS file for: " + curr)

            # check for valid exchange data
            if (dom.entries[0].cb_basecurrency == main_currency) and \
                    (dom.entries[0].cb_targetcurrency == curr):
                rate = dom.entries[0].cb_exchangerate.split('\n', 1)[0]
                rate_date_datetime = parser.parse(dom.entries[0].updated)\
                    .astimezone(pytz.utc).replace(tzinfo=None)
                self.check_rate_date(rate_date_datetime, max_delta_days)
                self.updated_currency[curr] = rate
                _logger.debug("BOC Rate retrieved : %s = %s %s" %
                              (main_currency, rate, curr))
            else:
                _logger.error(
                    "Exchange data format error for Bank of Canada -"
                    "%s. Please check provider data format "
                    "and/or source code." % curr)
                raise osv.except_osv('Error !',
                                     'Exchange data format error for\
                                     Bank of Canada - %s !' % str(curr))

        return self.updated_currency, self.log_info
    
#== Class to add CR rates  
class bccr_getter(Curreny_getter_interface):
    
    log_info = " "
    
    #Parse url
    def get_url(self, url):
        """Return a string of a get url query"""
        try:
            import urllib
            objfile = urllib.urlopen(url)
            rawfile = objfile.read()
            objfile.close()
            return rawfile
        except ImportError:
            raise osv.except_osv('Error !', self.MOD_NAME+'Unable to import urllib !')
        except IOError:
            raise osv.except_osv('Error !', self.MOD_NAME+'Web Service does not exist !')
    
    def get_updated_currency(self, cr, uid, currency_array, main_currency):
        
        logger2 = logging.getLogger('bccr_getter')
        """implementation of abstract method of Curreny_getter_interface"""
        today = time.strftime('%d/%m/%Y')
        url1='http://indicadoreseconomicos.bccr.fi.cr/indicadoreseconomicos/WebServices/wsIndicadoresEconomicos.asmx/ObtenerIndicadoresEconomicos?tcNombre=clearcorp&tnSubNiveles=N&tcFechaFinal=' + today + '&tcFechaInicio='
        url2='&tcIndicador='

        from xml.dom.minidom import parseString
        self.updated_currency = {} 

        
        for curr in currency_array :
            self.updated_currency[curr] = {}
            # Get the last rate for the selected currency
            currency_obj = pooler.get_pool(cr.dbname).get('res.currency')
            currency_rate_obj = pooler.get_pool(cr.dbname).get('res.currency.rate')
            currency_id = currency_obj.search(cr, uid, [('name','=',curr)])
            
            if not currency_id:
                continue
            
            currency = currency_obj.browse(cr, uid, currency_id)[0] #only one currency
            last_rate_id = currency_rate_obj.search(cr, uid, [('currency_id','in',currency_id)], order='name DESC', limit=1)
            last_rate = currency_rate_obj.browse(cr, uid, last_rate_id)
            #if len(last_rate):
             #   last_rate_date = last_rate[0].name
                #last_rate_date = datetime.strptime(last_rate_date,"%Y-%m-%d")
            #else:
            last_rate_date = today
            last_rate_datetime = time.strftime('%Y-%m-%d %H:%M:%S')
            url = url1 + last_rate_date + url2
           
            #=======Get code for rate
            url = url + currency.code_rate 
            list_rate = []
            logger2.info(url)
            rawstring = self.get_url(url)
            dom = parseString(rawstring)
            nodes = dom.getElementsByTagName('INGC011_CAT_INDICADORECONOMIC')
            for node in nodes:
                num_valor = node.getElementsByTagName('NUM_VALOR')
                if len(num_valor):
                    rate = num_valor[0].firstChild.data
                else:
                    continue
                des_fecha = node.getElementsByTagName('DES_FECHA')
                if len(des_fecha):
                    date_str = des_fecha[0].firstChild.data.split('T')[0]
                else:
                    continue
                if float(rate) > 0:
                   self.updated_currency[curr][last_rate_datetime] = rate   
        logger2.info(self.updated_currency)
        return self.updated_currency, self.log_info
