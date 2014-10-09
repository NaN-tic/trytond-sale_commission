# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from decimal import Decimal
from itertools import groupby

from trytond.model import ModelView, ModelSQL, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval

__all__ = ['Configuration', 'CommissionTypeCategory', 'CommissionType',
    'CommissionTypeParty', 'CommissionTypeLine', 'Party', 'Sale', 'Invoice',
    'InvoiceLine']
__metaclass__ = PoolMeta


class Configuration:
    __name__ = 'sale.configuration'

    commission_product = fields.Many2One('product.product',
        'Commission Product',
        domain=[
            ('type', '=', 'service'),
            ],
        help='Product used to generate in invoices for sale commisions.')


class CommissionTypeCategory(ModelSQL, ModelView):
    'Commission Category'
    __name__ = 'commission.type.category'

    name = fields.Char('Name', required=True, translate=True)


class CommissionTypeParty(ModelSQL):
    'CommissionType - Party'
    __name__ = 'commission.type-party.party'
    party = fields.Many2One('commission.type', 'CommissionType',
        ondelete='CASCADE', required=True, select=True)
    commission_type = fields.Many2One('party.party', 'PartyParty',
        ondelete='CASCADE', required=True, select=True)


class CommissionType(ModelSQL, ModelView):
    'Commission Type'
    __name__ = 'commission.type'

    name = fields.Char('Name', translate=True, required=True)
    lines = fields.One2Many('commission.type.line', 'commission_type',
        'Commission Lines', required=True)
    parties = fields.Many2Many('commission.type-party.party',
        'party', 'commission_type', 'Parties',
        domain=[
            ('is_middleman', '=', True),
            ])


class CommissionTypeLine(ModelSQL, ModelView):
    'CommissionType'
    __name__ = 'commission.type.line'

    commission_type = fields.Many2One('commission.type', 'Commission Type',
        required=True, select=True)
    category = fields.Many2One('commission.type.category', 'Category',
        required=True, select=True)
    percent = fields.Numeric('Percent', digits=(16, 4), required=True)

    def get_rec_name(self, name):
        return self.commission_type.rec_name

    @classmethod
    def __setup__(cls):
        super(CommissionTypeLine, cls).__setup__()
        cls._error_messages.update({
                'no_product_config': ('There is no product defined for '
                    'commissions. Please define on in sale configuration.'),
                'missing_expense_account': ('Product "%s" used to generate '
                    'commissions misses an expense account.'),
                'invoice_description': ('Commission "%(commission)s" for '
                    'payment of invoice %(invoice)s with date %(date)s.'),
                })

    def is_applicable_on_sale(self, sale):
        'Returns if the commission line is applicable for sale sale'
        return True

    def get_invoice_line(self, key, commissions):
        '''
        Returns the invoice line for the current commission

        Key is a dict with the key used to group commissions and
        commissions is a list of the commissions to group
        '''
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        Config = pool.get('sale.configuration')
        amount = sum(c['amount'] for c in commissions)
        if not amount:
            return
        config = Config(1)
        if not config.commission_product:
            self.raise_user_error('no_product_config')
        product = config.commission_product
        if not product.account_expense_used:
            self.raise_user_error('missing_expense_account', product.rec_name)
        line = InvoiceLine()
        line.party = key['middleman']
        line.type = 'line'
        line.invoice_type = 'in_invoice'
        line.description = self.get_invoice_line_description(key, commissions)
        line.product = product
        line.account = product.account_expense_used
        line.currency = InvoiceLine.default_currency()
        for k, v in line.on_change_product().iteritems():
            setattr(line, k, v)
        line.quantity = 1.0
        line.unit_price = Decimal(str(self.percent)) * amount
        line.origin = key['origin']
        return line

    def get_invoice_line_description(self, key, commissions):
        params = {
            'commission': self.rec_name,
            'invoice': '-'.join(c['invoice'] for c in commissions),
            'date': key['date'],
            }
        return self.raise_user_error('invoice_description', params,
                raise_exception=False)


class Party:
    __name__ = 'party.party'

    is_middleman = fields.Boolean('Middleman', help='Indicates if the party'
        ' is a middleman and gets commissions.')
    middleman = fields.Many2One('party.party', 'Middleman',
        domain=[
            ('is_middleman', '=', True),
            ],
        states={
            'invisible': Bool(Eval('is_middleman')),
            },
        depends=['middleman'])
    commissions = fields.Many2Many('commission.type-party.party',
        'commission_type', 'party', 'Commissions',
        states={
            'invisible': ~Bool(Eval('is_middleman')),
            },
        depends=['middleman'])


class Sale:
    __name__ = 'sale.sale'

    middleman = fields.Many2One('party.party', 'Middleman',
        domain=[
            ('is_middleman', '=', True),
            ],
        states={
            'readonly': Eval('state') != 'draft',
            },
        depends=['state'])
    commission_type = fields.Many2One('commission.type', 'Commission Type',
        states={
            'invisible': ~Bool(Eval('middleman')),
            'required': Bool(Eval('middleman')),
            'readonly': Eval('state') != 'draft',
            },
        depends=['middleman', 'state'])

    @fields.depends('middleman')
    def on_change_party(self):
        changes = super(Sale, self).on_change_party()
        changes['middleman'] = None
        self.middleman = None
        if self.party and self.party.middleman:
            changes['middleman'] = self.party.middleman.id
            changes['middleman.rec_name'] = self.party.middleman.rec_name
            self.middleman = self.party.middleman
        changes.update(self.on_change_middleman())
        return changes

    @fields.depends('middleman')
    def on_change_middleman(self):
        changes = {
            'commission_type': None,
            }
        if self.middleman and self.middleman.commissions:
            changes['commission_type'] = self.middleman.commissions[0].id
        return changes


class Invoice:
    __name__ = 'account.invoice'

    @classmethod
    def _group_commission_key(cls, commission):
        '''
        The key to group commissions

        commission is a dict with the values to create the commission
        '''
        return (
            ('middleman', commission['middleman']),
            ('date', commission['date']),
            ('origin', commission['origin']),
            ('commission', commission['commission']),
            )

    @classmethod
    def process(cls, invoices):
        pool = Pool()
        InvoiceLine = pool.get('account.invoice.line')
        Currency = pool.get('currency.currency')
        super(Invoice, cls).process(invoices)
        commissions = []

        for invoice in invoices:
            if not invoice.move:
                continue
            middlemans_commissions = set()
            for sale in invoice.sales:
                if not sale.middleman or not sale.commission_type:
                    continue
                for line in sale.commission_type.lines:
                    if not line.is_applicable_on_sale(sale):
                        continue
                    middlemans_commissions.add((sale.middleman, line))
            if not middlemans_commissions:
                continue
            total = Currency.compute(invoice.currency, invoice.untaxed_amount,
                invoice.company.currency)
            term_lines = invoice.payment_term.compute(total,
                invoice.company.currency, invoice.invoice_date)
            if not term_lines:
                term_lines = [(invoice.invoice_date, total)]
            for (date, amount), line in zip(term_lines, invoice.lines_to_pay):
                if not line.reconciliation:
                    continue
                existing_lines = InvoiceLine.search([
                            ('origin', '=', str(line)),
                            ])
                if existing_lines:
                    continue
                for middleman, commission in middlemans_commissions:
                    commissions.append({
                            'amount': amount,
                            'date': date,
                            'middleman': middleman.id,
                            'commission': commission,
                            'invoice': invoice.rec_name,
                            'origin': str(line),
                            })

        keyfunc = cls._group_commission_key
        commissions = sorted(commissions, key=keyfunc)
        to_create = []
        for key, grouped_commissions in groupby(commissions, key=keyfunc):
            key = dict(key)
            commission = key['commission']
            grouped_commissions = list(grouped_commissions)
            line = commission.get_invoice_line(key, grouped_commissions)
            to_create.append(line._save_values)
        if to_create:
            InvoiceLine.create(to_create)


class InvoiceLine:
    __name__ = 'account.invoice.line'

    @classmethod
    def _get_origin(cls):
        origins = super(InvoiceLine, cls)._get_origin()
        origins.append('account.move.line')
        return origins
