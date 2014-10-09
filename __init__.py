# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from .sale import *


def register():
    Pool.register(
        Configuration,
        CommissionTypeCategory,
        CommissionType,
        CommissionTypeParty,
        CommissionTypeLine,
        Party,
        Sale,
        Invoice,
        InvoiceLine,
        module='sale_commission', type_='model')
