import json
from pathlib import Path
import unittest

import test_pipeline as fixture
from test_pipeline import relay, storage
from tools.market_impact import assess_impact, attach_impact


def story(title, symbols=('ACME',), snippet=''):
    return {'title':title, 'snippet':snippet, 'entities':[{'symbol':s} for s in symbols]}


class MarketImpactTests(unittest.TestCase):
    def test_real_headlines_and_counterexamples(self):
        cases = [
            ('Amazon Announces Black Friday Week, Cyber Monday Events Will Return November 19-30 With Millions Of Deals, Daily Drops Up To 60% Off', 'unknown'),
            ('SoftBank Group shares surge 13% as Asian tech stocks track Wall Street rebound', 'unknown'),
            ('Acme Launches New Products This Fall', 'unknown'),
            ('Acme Wins Design Award, Rewards Customers With Discounts', 'unknown'),
            ('Acme Shares Drop 8% As Investors Sell', 'unknown'),
            ('Acme Q3 EPS $1.42 Beats $1.39 Estimate, Sales $570.165M Beat $565.982M Estimate', 'positive'),
            ('Acme Q3 EPS $0.16 Misses $0.25 Estimate, Sales $22.738B Beat $22.629B Estimate', 'mixed'),
            ('Sanmina Surges 20% After-Hours as Q4 Earnings Beat Estimates, Fiscal Q1 Outlook Tops Expectations', 'positive'),
            ('Acme EPS Beats Estimates But Cuts FY2027 Revenue Guidance', 'mixed'),
            ('Acme Reports Record Revenue, Profit Rises 40% Year Over Year', 'unknown'),
            ('Acme Reports EPS In Line With Estimates', 'unknown'),
            ('Acme Q3 EPS $1.50 vs $1.20 Est', 'positive'),
            ('Acme Q3 EPS $1.10 vs $1.20 Est', 'negative'),
            ('Acme Q3 EPS $1.20 vs $1.20 Est', 'unknown'),
            ('Acme Q3 EPS ($0.20) vs ($0.30) Est', 'positive'),
            ('Acme Q3 EPS ($0.40) vs ($0.30) Est', 'negative'),
            ('Acme Q3 Revenue $0.9B vs $950M Est', 'negative'),
            ('Acme Sees FY EPS $2.0-$2.4 vs $2.2 Est', 'unknown'),
            ('Acme Sees FY EPS $2.3-$2.4 vs $2.2 Est', 'positive'),
            ('Acme Sees FY EPS $1.8-$2.0 vs $2.2 Est', 'negative'),
            ('Acme Raises FY2027 EPS Guidance', 'positive'),
            ('Acme Lowers Full Year Revenue Outlook', 'negative'),
            ('Acme Raises Capital Spending Guidance', 'unknown'),
            ('Acme Maintains Buy Rating, Raises Price Target', 'unknown'),
            ('Goldman Sachs Upgrades Acme To Buy', 'positive'),
            ('Goldman Sachs Downgrades Acme To Hold', 'negative'),
            ('Acme Announces $1 Billion Share Repurchase Program', 'positive'),
            ('Acme Announces $1 Billion Public Offering Of Common Stock', 'negative'),
            ('Acme Announces $1 Billion Bond Offering', 'unknown'),
            ('Acme Announces Common Stock Offering By Selling Stockholders', 'unknown'),
            ('Acme Files For Chapter 11 Bankruptcy', 'negative'),
            ('FDA Approves Acme Cancer Drug', 'positive'),
            ('FDA Rejects Acme Cancer Drug', 'negative'),
            ('Acme Receives FDA Approval For Cancer Drug', 'positive'),
            ('Acme Receives Complete Response Letter From FDA', 'negative'),
            ('FDA Approves Acme Rival Cancer Drug', 'unknown'),
            ('Acme May Raise EPS Guidance', 'unknown'),
            ('Acme Could Beat Earnings Estimates', 'unknown'),
            ('What If FDA Approves Acme Cancer Drug?', 'unknown'),
            ('Acme Denies Report That It Filed For Bankruptcy', 'unknown'),
            ('Acme Did Not Raise EPS Guidance', 'unknown'),
            ('Acme Is Expected To Receive FDA Approval', 'unknown'),
            ('Acme Plans To Announce Share Buyback', 'unknown'),
            ('Reported Earlier, Acme EPS Beats Estimates', 'unknown'),
            ('Acme Raised EPS Guidance Last Year', 'unknown'),
            ('Acme Earnings Preview: Analysts Expect EPS Beat', 'unknown'),
            ('Acme Predicts It Beats Earnings Estimates', 'unknown'),
        ]
        for title, direction in cases:
            with self.subTest(title=title):
                impact = assess_impact(story(title))
                self.assertEqual(impact['direction'], direction)
                if direction != 'unknown':
                    self.assertEqual(impact['scope'], 'company')
                    self.assertEqual(impact['targets'], ['ACME'])
                    self.assertTrue(impact['reason'])
                    self.assertTrue(impact['evidence'])
                    for evidence in impact['evidence']:
                        self.assertIn(evidence['quote'], title)

    def test_macro_scope_expectations_and_opposing_channels(self):
        cases = [
            ('U.S. CPI 2.8% vs 3.0% Est', 'positive'),
            ('U.S. CPI 3.2% vs 3.0% Est', 'negative'),
            ('U.S. CPI 3.0% vs 3.0% Est', 'unknown'),
            ('U.S. May CPI 2.8% vs 3.0% Est', 'positive'),
            ('U.S. CPI Falls To 3.0% From 3.5%', 'unknown'),
            ('U.S. Core PCE Above Expectations', 'negative'),
            ('U.S. CPI Below Expectations; Core CPI Above Expectations', 'mixed'),
            ('China CPI Below Expectations', 'unknown'),
            ('U.S. Stocks Rise As China CPI Below Expectations', 'unknown'),
            ('U.S. CPI Released; Prior 3.2% vs 3.0% Est', 'unknown'),
            ('U.K. Retail Sales 1.1% vs 1.2% Est', 'unknown'),
            ('Fed Cuts Interest Rates By 25 Basis Points', 'positive'),
            ('Fed Raises Interest Rates By 25 Basis Points', 'negative'),
            ('Fed Cuts Rates As Expected', 'unknown'),
            ('Fed Cuts Rates Amid Banking Crisis', 'mixed'),
            ('Fed May Cut Rates In December', 'unknown'),
            ('Trump Calls For Fed To Cut Rates', 'unknown'),
            ('U.S. Nonfarm Payrolls Above Expectations', 'mixed'),
            ('U.S. GDP Below Expectations', 'mixed'),
            ('Oil Prices Fall As Demand Weakens', 'unknown'),
            ('Treasury Yields Fall Amid Recession Concerns', 'unknown'),
            ('Trump Sees Positive Momentum To End War, Talks Continue', 'unknown'),
        ]
        for title, direction in cases:
            with self.subTest(title=title):
                impact = assess_impact(story(title, ('SPY', 'QQQ')))
                self.assertEqual(impact['direction'], direction)
                if direction != 'unknown':
                    self.assertEqual(impact['scope'], 'us_equities')
                    self.assertEqual(impact['targets'], [])

    def test_subject_is_not_guessed_from_multiple_tickers_or_indices(self):
        for symbols in [(), ('ACME', 'PEER'), ('SPY',), ('X:BTCUSD',)]:
            with self.subTest(symbols=symbols):
                self.assertEqual(assess_impact(story('Acme EPS Beats Estimates', symbols))['direction'], 'unknown')

    def test_teaser_conflict_is_kept_and_cannot_initiate_a_prediction(self):
        item = story('Acme EPS Beats Estimates', snippet='Acme lowers FY revenue guidance.')
        self.assertEqual(assess_impact(item)['direction'], 'mixed')
        item['snippet'] = 'Acme denies reports that it raised EPS guidance.'
        self.assertEqual(assess_impact(item)['direction'], 'unknown')
        item['title'] = 'Acme Announces Black Friday Deals'
        item['snippet'] = 'Last year EPS beat estimates.'
        self.assertEqual(assess_impact(item)['direction'], 'unknown')

    def test_mixed_reason_exposes_the_counterargument_not_only_positive_metrics(self):
        impact = assess_impact(story('Acme EPS Beats Estimates, Sales Beat Estimates; Cuts FY Revenue Guidance'))
        self.assertEqual(impact['direction'], 'mixed')
        self.assertIn('상회', impact['reason'])
        self.assertIn('하향', impact['reason'])

    def test_original_text_drives_estimate_not_translation_or_legacy_label(self):
        item = story('Acme EPS Misses Estimates')
        item.update(titleKo='실적 예상 상회', sentiment='positive')
        self.assertEqual(attach_impact(item)['sentiment'], 'negative')
        item = story('Acme Announces Black Friday Deals')
        item['sentiment'] = 'negative'
        self.assertEqual(attach_impact(item)['sentiment'], 'neutral')
        self.assertEqual(attach_impact(item)['marketImpact']['direction'], 'unknown')

    def test_browser_examples_are_real_classifier_output(self):
        for item in json.loads(Path(__file__).with_name('market_impact_examples.json').read_text(encoding='utf-8')):
            self.assertEqual(assess_impact(item), item['marketImpact'])


class ImpactPipelineTests(unittest.TestCase):
    setUp = fixture.PipelineTests.setUp

    def test_cached_snapshot_is_reassessed_on_outage_and_keeps_article_time(self):
        item = dict(self.item, **story('Amazon Announces Daily Drops Up To 60% Off', ('AMZN',)), sentiment='negative')
        storage.atomic_write_json(Path(self.args.out), {'items':[item], 'updatedAt':self.now.isoformat()})
        relay.write_payload(Path(self.args.out), [], {'source_statuses':{'massive':{'status':'error'}}})
        saved = storage.read_json(Path(self.args.out))
        self.assertEqual(saved['items'][0]['sentiment'], 'neutral')
        self.assertEqual(saved['items'][0]['marketImpact']['direction'], 'unknown')
        self.assertEqual(saved['updatedAt'], self.now.isoformat())

    def test_new_feed_exposes_target_evidence_and_qualified_direction(self):
        item = dict(self.item, **story('Acme EPS Beats Estimates'))
        relay.write_payload(Path(self.args.out), [item])
        saved = storage.read_json(Path(self.args.out))['items'][0]
        self.assertEqual(saved['marketImpact']['direction'], 'positive')
        self.assertEqual(saved['marketImpact']['targets'], ['ACME'])
        self.assertIn('EPS Beats Estimates', saved['marketImpact']['evidence'][0]['quote'])


if __name__ == '__main__':
    unittest.main()
