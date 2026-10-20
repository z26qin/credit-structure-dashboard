# Overrides

Optional, hand-written inputs the free data sources don't give. One file per ticker:
`overrides/<TICKER>.yaml`. Every key is optional; money is in **$ millions**.

```yaml
price: 62.36                 # share price; when set, Yahoo is not asked
consensus:                   # per fiscal year (named by the calendar year it ends in)
  2027: {revenue: 26311, ebitda: 16635, capex: 47029, cfo: 14613}
  2028: {revenue: 42818, ebitda: 28681, capex: 51536, cfo: 29824}
ratings:                     # by instrument name, as the dashboard shows it
  DDTL 4.0 Facility: A3
bond_yields:                 # yield-to-worst %, as quoted by a bond data site
  2032 9.625% Senior Notes: 14.2
ebitda_label: adj. EBITDA    # when consensus EBITDA is an adjusted figure
```

With consensus present, the dashboard adds a consensus scenario and uses consensus capex and
operating cash flow for "cash to raise"; without it, trailing-twelve-month figures are held flat.

`cds_5y_bps: 650` (5-year CDS, basis points) drives the market-implied default card at the top of the page;
without it the highest `bond_yields` entry less SOFR is used. Recovery is `recovery_rate` in
`config/model.yaml` (default 40%).
