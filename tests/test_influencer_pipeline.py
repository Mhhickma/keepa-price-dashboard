import copy
import csv
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import influencer_pipeline as p

NOW = 1788739200


def minute(days=0):
    return int((NOW - days*p.DAY - p.EPOCH)/60)


def product(asin="B000000001"):
    current = [-1]*19
    current[18], current[3] = 4999, 1000
    return dict(asin=asin, productType=0, title="Cordless drill", brand="Example",
        categoryTree=[{"catId":1,"name":"Tools & Home Improvement"}],
        stats={"current":current}, monthlySold=110, lastSoldUpdate=minute(),
        monthlySoldHistory=[minute(100),100,minute(),110],
        csv=[None,None,None,[minute(100),2000,minute(),1000]],
        salesRankReference=1, videos=[{"url":"https://example.test/video","creator":"Seller"}],
        offersSuccessful=True)


def row(**changes):
    base = {"Campaign Id":"c1","Campaign Name":"Tools, demos\nSeptember", "Brand Name":"Example",
        "Campaign Start Date":"2026-01-01", "Campaign End Date":"2030-01-01", "Commission Rate":"10%",
        "Campaign Budget":"$10,000", "Budget Remaining":"$5,000", "Available Slots":"20", "Total Slots":"100",
        "Recommended":"true", "ASIN List":"B000000001, B000000002"}
    base.update(changes)
    return base


class Qualification(unittest.TestCase):
    def evaluate(self, product_value=None):
        return p.evaluate(product_value or product(), [p.campaign(row())], NOW, NOW)

    def test_exact_ten_percent_passes(self):
        result=self.evaluate()
        self.assertTrue(result["qualified"])
        self.assertEqual(result["monthly_sold_90"],100)
        self.assertAlmostEqual(result["growth"],10)
        self.assertEqual(result["bsr90"],50)

    def test_bsr_cannot_replace_missing_sales(self):
        value=product(); del value["monthlySoldHistory"]
        result=self.evaluate(value)
        self.assertFalse(result["qualified"])
        self.assertEqual(result["sales_trend"],"unavailable")
        self.assertIsNone(result["score"])
        self.assertEqual(result["bsr90"],50)

    def test_partial_history_missing_gap_zero_and_stale(self):
        for history in ([minute(89),100], [minute(100),100,minute(50),-1,minute(1),100], [minute(100),0]):
            value=product(); value["monthlySoldHistory"]=history
            self.assertFalse(self.evaluate(value)["qualified"])
        value=product(); value["lastSoldUpdate"]=minute(31)
        self.assertEqual(self.evaluate(value)["sales_trend"],"unavailable")

    def test_time_weighted_average(self):
        self.assertEqual(p.average([minute(100),100,minute(45),200],NOW,90),150)

    def test_video_unknown_and_five_fail(self):
        for videos in (None, [], [{"creator":"Main","url":"x"}], [{"creator":"Seller"}], [{"creator":"Seller","url":str(i)} for i in range(5)]):
            value=product();value["videos"]=videos
            self.assertFalse(self.evaluate(value)["qualified"])
        value=product();value["offersSuccessful"]=False
        self.assertFalse(self.evaluate(value)["qualified"])

    def test_influencer_and_merchant_are_distinct(self):
        value=product();value["videos"] += [{"url":"y","creator":"Influencer"},{"url":"z","creator":"Customer"}]
        result=self.evaluate(value)
        self.assertEqual(result["influencer_videos"],1)
        self.assertEqual(result["community_videos"],2)
        self.assertEqual(result["total_videos"],3)

    def test_apparel_and_functional_gear(self):
        for title, nodes, excluded in [("T-shirt",["Clothing, Shoes & Jewelry","Clothing"],True),
            ("Safety vest PPE",["Clothing, Shoes & Jewelry","Clothing"],False),
            ("Work gloves",["Tools & Home Improvement"],False),
            ("Pet toy",["Pet Supplies"],False),("Camera",["Electronics"],False),
            ("Camping stove",["Sports & Outdoors"],False),("Pendant",["Clothing, Shoes & Jewelry","Jewelry"],False)]:
            value=product();value["title"]=title;value["categoryTree"]=[{"name":n} for n in nodes]
            self.assertEqual(p.apparel(value),excluded)

    def test_expired_cache_fails(self):
        self.assertFalse(p.evaluate(product(),[p.campaign(row())],NOW,NOW-25*3600)["qualified"])

    def test_evaluator_rechecks_campaign_and_nullable_categories(self):
        self.assertFalse(p.evaluate(product(),[p.campaign(row(**{'Commission Rate':'9%'}))],NOW,NOW)['qualified'])
        value=product();value['categoryTree']=None
        self.assertFalse(self.evaluate(value)['qualified'])

    def test_reference_change_suppresses_bsr(self):
        value=product();value["salesRankReferenceHistory"]=[minute(10),2]
        self.assertIsNone(self.evaluate(value)["bsr90"])

    def test_campaign_dates_and_commission(self):
        for changes in ({"Campaign Start Date":"bad"},{"Campaign End Date":""},{"Commission Rate":"9.99%"},
                        {"Campaign Start Date":"2031-01-01"},{"Campaign End Date":"2025-01-01"},{"Status":"Paused"}):
            self.assertFalse(p.active(p.campaign(row(**changes)),"2026-09-07"))
        self.assertTrue(p.active(p.campaign(row(**{"Commission Rate":"0.10"})),"2026-09-07"))


class Storage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.input=self.root/"input";self.input.mkdir()
        self.state=self.root/"checkpoint.sqlite"
        self.db=p.connect(self.state)
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def write(self,name,rows):
        with (self.input/name).open("w",encoding="utf-8-sig",newline="") as file:
            writer=csv.DictWriter(file,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
    def ingest(self):
        self.assertTrue(p.import_sources(self.db,sorted(self.input.glob("*.csv")),time.monotonic()+60))
        p.eligible_index(self.db,"2026-09-07")
    def test_multifile_union_and_best_commission(self):
        self.write("a.csv",[row()]); self.write("b.csv",[row(**{"Campaign Id":"c2","Commission Rate":"15%","Recommended":"false"})])
        self.ingest(); campaigns=p.campaigns_for(self.db,"B000000001")
        result=p.evaluate(product(),campaigns,NOW,NOW)
        self.assertEqual(len(campaigns),2); self.assertEqual(result["commission"],15)
        self.assertEqual(campaigns[0]["budget_remaining"],5000)
        self.assertTrue(any("\n" in c["name"] for c in campaigns))
        self.ingest();self.assertEqual(len(p.campaigns_for(self.db,"B000000001")),2)
    def test_latest_cancellation_invalidates_older(self):
        self.write("a.csv",[row()]); self.write("20260907T120000.csv",[row(**{"Status":"Cancelled"})])
        self.ingest(); self.assertEqual(p.campaigns_for(self.db,"B000000001"),[])
    def test_same_campaign_different_asin_lists_union(self):
        self.write("a.csv",[row(**{"ASIN List":"B000000001"})]); self.write("b.csv",[row(**{"ASIN List":"B000000002"})])
        self.ingest(); self.assertEqual(len(p.campaigns_for(self.db,"B000000001")),1)
    def test_checkpoint_resumes_import(self):
        self.write("a.csv",[row(**{"Campaign Id":str(i)}) for i in range(1100)])
        self.assertFalse(p.import_sources(self.db,list(self.input.glob("*.csv")),0))
        self.assertEqual(self.db.execute("SELECT rownum FROM sources").fetchone()[0],500)
        self.ingest();self.assertEqual(self.db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0],1100)
    def test_cached_resume_makes_no_api_call(self):
        self.write("a.csv",[row(**{"ASIN List":"B000000001"})]);self.ingest()
        self.db.execute("INSERT INTO selected VALUES('B000000001')")
        self.db.execute("INSERT INTO cache VALUES(?,?,?)",("B000000001",time.time(),json.dumps(product())))
        self.db.commit()
        with patch.dict('os.environ',{'KEEPA_API_KEY':'test-only'}),patch.object(p.Keepa,'fetch') as fetch:
            result=p.main(['--input',str(self.input),'--state',str(self.state),'--output',str(self.root/'out')])
            fetch.assert_not_called();self.assertEqual(result['selected'],1)
    def test_fixed_cohort_across_resumes(self):
        self.write("a.csv",[row(**{"ASIN List":' '.join(f'B{i:09}' for i in range(150))})])
        args=['--input',str(self.input),'--state',str(self.state),'--output',str(self.root/'out'),'--offline']
        self.assertEqual(p.main(args)['selected'],100)
        self.assertEqual(p.main(args)['selected'],100)

    def test_large_source_still_selects_only_100(self):
        self.write('large.csv',[row(**{'ASIN List':' '.join(f'B{i:09}' for i in range(100000))})])
        result=p.main(['--input',str(self.input),'--state',str(self.state),'--output',str(self.root/'out'),'--offline'])
        self.assertEqual(result['selected'],100)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM links').fetchone()[0],100000)


class Requests(unittest.TestCase):
    def test_missing_token_telemetry_is_unavailable(self):
        response=Mock(status_code=200);response.json.return_value={'products':[product()]}
        session=Mock();session.get.return_value=response
        api=p.Keepa('test',time.monotonic()+1000,100,session)
        api.fetch(['B000000001'])
        self.assertTrue(api.usage_unknown)
        self.assertIsNone(api.balance)

    def test_retry_budget_and_no_secret_in_error(self):
        session=Mock();session.get.side_effect=p.requests.ConnectionError('https://api.keepa.com/?key=private')
        api=p.Keepa('private',time.monotonic()+1000,12,session,refresh=True)
        with patch.object(p.time,'sleep'):
            products,error=api.fetch(['B000000001'])
        self.assertEqual(error,'paused_budget_or_time');self.assertEqual(session.get.call_count,1)
    def test_rate_limit_then_success_and_telemetry(self):
        session=Mock()
        rate=Mock(status_code=429,headers={'Retry-After':'1'});rate.json.return_value={'refillIn':2000,'tokensLeft':0,'tokensConsumed':0}
        ok=Mock(status_code=200,headers={});ok.json.return_value={'products':[product()],'tokensLeft':50,'tokensConsumed':6}
        session.get.side_effect=[rate,ok]
        api=p.Keepa('test',time.monotonic()+1000,100,session)
        with patch.object(p.time,'sleep') as sleep:
            products,error=api.fetch(['B000000001'])
        self.assertIsNone(error);self.assertEqual(api.consumed,6);self.assertEqual(api.balance,50);sleep.assert_called_once()
    def test_missing_product_logged(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'input';source.mkdir()
            with (source/'a.csv').open('w',newline='') as f:
                r=row(**{'ASIN List':'B000000001'});writer=csv.DictWriter(f,fieldnames=r);writer.writeheader();writer.writerow(r)
            with patch.dict('os.environ',{'KEEPA_API_KEY':'test'}),patch.object(p.Keepa,'fetch',return_value=([],None)):
                result=p.main(['--input',str(source),'--state',str(root/'s.sqlite'),'--output',str(root/'out')])
            self.assertEqual(result['failed'],1)
            self.assertEqual(json.loads((root/'out/failures.json').read_text())[0]['code'],'product_missing')


if __name__ == '__main__': unittest.main()


class ScanCostTests(unittest.TestCase):
    def test_normal_scan_never_requests_offers(self):
        session=Mock(); session.get.return_value.status_code=200
        session.get.return_value.json.return_value={'products':[], 'tokensConsumed':1}
        api=p.Keepa('test',time.monotonic()+1000,1,session)
        api.fetch(['B000000001'])
        self.assertNotIn('offers',session.get.call_args.kwargs['params'])
        self.assertEqual(api.reserved,1)

    def test_explicit_refresh_requests_offers(self):
        session=Mock(); session.get.return_value.status_code=200
        session.get.return_value.json.return_value={'products':[], 'tokensConsumed':6}
        api=p.Keepa('test',time.monotonic()+1000,12,session,refresh=True)
        api.fetch(['B000000001'])
        self.assertEqual(session.get.call_args.kwargs['params']['offers'],20)
        self.assertEqual(api.reserved,12)

    def test_stored_videos_do_not_require_refresh(self):
        value=product(); value.pop('offersSuccessful')
        self.assertTrue(p.evaluate(value, [p.campaign(row())], NOW, NOW, 24)['merchant_video'])
