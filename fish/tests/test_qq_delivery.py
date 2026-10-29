import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import websocket
except ImportError:
    sys.modules['websocket'] = Mock()

from memory import Store
from qq_bot import QQBot, cq_text, cq_face
from qq_delivery import unit_text, signature
from qq_faces import FACE_IDS
from delivery import SendUnconfirmedError
from qq_config import delivery_config
import qq_media


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.store.set_setting('enabled_platforms',['qq'])
        self.store.set_setting('owner_qq','101')
        self.bot = QQBot('ws://unused',Mock(),self.store)
        self.bot.self_id = 999
        self.ok = {'retcode':0,'data':{}}

    def tearDown(self):
        self.store.close()

    def receipt(self,text='FIRST',**kwargs):
        return dict(post_type='message_sent',message_type='private',self_id=999,user_id=999,target_id=101,
                    sender={'user_id':999},time=int(time.time()),message_id=123,
                    message=[cq_text(text)],**kwargs)

    def states(self):
        return [r[0] for r in self.store._conn.execute('SELECT status FROM qq_outbox ORDER BY id')]

    def test_named_and_legacy_numeric_faces_are_native_segments(self):
        for label, fid in [('白眼',22),('22',22),('4',4),('23',23),('24',24),('29',29),('0',0)]:
            with self.subTest(label=label):
                self.assertEqual(self.bot._to_segments(f'好[表情:{label}]呀'),
                                 [cq_text('好'),cq_face(fid),cq_text('呀')])
        for label in ('未知名字','-1','2.2','２２'):
            with self.subTest(label=label):
                tag=f'[表情:{label}]'
                self.assertEqual(self.bot._to_segments(tag),[cq_text(tag)])

    def test_face_history_round_trip_preserves_native_payload(self):
        for fid in list(FACE_IDS.values())+[23,24,29]:
            with self.subTest(fid=fid):
                original=[cq_text('好'),cq_face(fid),cq_text('呀')]
                self.assertEqual(self.bot._to_segments(unit_text(original)),original)
                self.assertEqual(signature(self.bot._to_segments(unit_text(original))),signature(original))
        self.assertEqual(unit_text([cq_face(22)]),'[表情:白眼]')

    def test_numeric_face_send_writes_reusable_named_history(self):
        self.bot.send_msg=Mock(return_value=self.ok)
        self.bot._send_segments(101,['好[表情:22]'])
        self.bot.send_msg.assert_called_once_with(101,[cq_text('好'),cq_face(22)],'private')
        history=self.store.get_short_term()[0]['content']
        self.assertEqual(history,'好[表情:白眼]')
        self.assertEqual(self.bot._to_segments(history),[cq_text('好'),cq_face(22)])

    def test_first_part_timeout_late_self_receipt_continues_exactly_once(self):
        calls=[]
        def send(target,unit,kind):
            calls.append(unit)
            if len(calls)==1:
                self.bot._receipts.append(self.receipt())
                raise SendUnconfirmedError('native timeout')
            return self.ok
        self.bot.send_msg=Mock(side_effect=send)
        with patch('qq_bot.MSG_SEND_DELAY',0):
            self.bot._send_segments(101,['FIRST','SECOND','THIRD'])
        self.assertEqual(len(calls),3)
        self.assertEqual(self.states(),['confirmed']*3)
        self.assertEqual([h['content'] for h in self.store.get_short_term()],['FIRST','SECOND','THIRD'])

    def test_unknown_first_part_is_not_replayed_or_silently_forgotten(self):
        self.bot.send_msg=Mock(side_effect=SendUnconfirmedError('no ack'))
        with patch('qq_bot.SEND_LATE_GRACE',0),self.assertRaises(SendUnconfirmedError):
            self.bot._send_segments(101,['FIRST','SECOND'])
        self.assertEqual(self.bot.send_msg.call_count,1)
        self.assertEqual(self.states(),['unconfirmed','cancelled'])
        history=self.store.get_short_term()
        self.assertEqual(len(history),1)
        self.assertIn('未确认',history[0]['content'])
        self.assertIn('FIRST',history[0]['content'])
        self.assertTrue(self.store.delivery_confirm_event(self.receipt()))
        self.assertEqual(self.store.get_short_term()[0]['content'],'FIRST')
        self.assertEqual(self.bot.send_msg.call_count,1)

    def test_confirmed_prefix_survives_second_part_failure(self):
        self.bot.send_msg=Mock(side_effect=[self.ok,SendUnconfirmedError('unknown')])
        with patch('qq_bot.SEND_LATE_GRACE',0),patch('qq_bot.MSG_SEND_DELAY',0),self.assertRaises(SendUnconfirmedError):
            self.bot._send_segments(101,['FIRST','SECOND','THIRD'])
        self.assertEqual(self.states(),['confirmed','unconfirmed','cancelled'])
        history=self.store.get_short_term()
        self.assertEqual(history[0]['content'],'FIRST')
        self.assertIn('SECOND',history[1]['content'])
        self.assertNotIn('THIRD',str(history))

    def test_new_message_cancels_tail_after_late_receipt(self):
        def send(*args):
            self.bot._receipts.append(self.receipt())
            self.bot._conversation_versions['private_101']=1
            raise SendUnconfirmedError('unknown')
        self.bot.send_msg=Mock(side_effect=send)
        with patch('qq_bot.MSG_SEND_DELAY',0),self.assertRaises(RuntimeError):
            self.bot._send_segments(101,['FIRST','SECOND'])
        self.assertEqual(self.bot.send_msg.call_count,1)
        self.assertEqual(self.states(),['confirmed','cancelled'])

    def test_reset_during_send_cannot_restore_context_or_send_tail(self):
        def send(*args):
            self.store.clear_memories()
            self.store.clear_short_term()
            return self.ok
        self.bot.send_msg=Mock(side_effect=send)
        with patch('qq_bot.MSG_SEND_DELAY',0),self.assertRaises(RuntimeError):
            self.bot._send_segments(101,['FIRST','SECOND'])
        self.assertEqual(self.bot.send_msg.call_count,1)
        self.assertEqual(self.store.get_short_term(),[])
        self.assertEqual(self.store._conn.execute('SELECT content FROM qq_outbox LIMIT 1').fetchone()[0],'')

    def test_disabled_platform_and_identity_edit_stop_tail(self):
        for change in ({'enabled_platforms':[]},{'persona':'changed'}):
            self.store.set_setting('enabled_platforms',['qq'])
            self.bot.send_msg=Mock(side_effect=lambda *a:(self.store.set_settings(change) or self.ok))
            with patch('qq_bot.MSG_SEND_DELAY',0),self.assertRaises(RuntimeError):
                self.bot._send_segments(101,['FIRST','SECOND'])
            self.assertEqual(self.bot.send_msg.call_count,1)

    def test_ambiguous_wrong_target_old_time_and_replayed_receipt_do_not_confirm(self):
        rid=self.store.delivery_plan(999,'private_101',[[cq_text('FIRST')]],0)[0]
        self.store.delivery_state(rid,'sending')
        for changes in ({'target_id':102},{'self_id':998},{'time':time.time()-1000},
                        {'sender':{'user_id':101}},{'message':[cq_text('different')]}):
            event=self.receipt();event.update(changes)
            self.assertFalse(self.store.delivery_confirm_event(event))
        self.assertTrue(self.store.delivery_confirm_event(self.receipt()))
        other=self.store.delivery_plan(999,'private_101',[[cq_text('FIRST')]],0)[0]
        self.store.delivery_state(other,'sending')
        self.assertFalse(self.store.delivery_confirm_event(self.receipt()))
        another=self.store.delivery_plan(999,'private_101',[[cq_text('FIRST')]],0)[0]
        self.store.delivery_state(another,'sending')
        event=self.receipt();event['message_id']=124
        self.assertFalse(self.store.delivery_confirm_event(event))

    def test_missing_private_target_does_not_guess_destination(self):
        rid=self.store.delivery_plan(999,'private_101',[[cq_text('FIRST')]],0)[0]
        self.store.delivery_state(rid,'sending')
        event=self.receipt();event.pop('target_id')
        self.assertFalse(self.store.delivery_confirm_event(event))

    def test_late_exact_echo_confirms_without_payload_heuristic(self):
        rid=self.store.delivery_plan(999,'private_101',[[cq_text('FIRST')]],0)[0]
        self.store.delivery_state(rid,'sending')
        self.store.delivery_state(rid,'unconfirmed')
        self.bot._on_event({'post_type':'delivery_ack','delivery_id':rid,'message_id':123})
        self.assertEqual(self.store.delivery_status(rid),'confirmed')

    def test_real_send_api_accepts_self_receipt_without_waiting_for_timeout(self):
        ws=Mock()
        def send(raw):
            obj=json.loads(raw)
            event=self.receipt()
            event['message']=obj['params']['message']
            event['message_id']=1000+ws.send.call_count
            self.bot._receipts.append(event)
        ws.send.side_effect=send
        self.bot._ws=ws
        started=time.monotonic()
        with patch('qq_bot.MSG_SEND_DELAY',0):
            self.bot._send_segments(101,['FIRST','SECOND'])
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual(ws.send.call_count,2)
        self.assertEqual(self.states(),['confirmed','confirmed'])

    def test_new_message_interrupts_api_wait_without_resending(self):
        self.bot._ws=Mock()
        self.bot._ws.send.side_effect=lambda raw:self.bot._conversation_versions.update(private_101=1)
        started=time.monotonic()
        with self.assertRaises(SendUnconfirmedError):
            self.bot._send_segments(101,['FIRST','SECOND'])
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual(self.bot._ws.send.call_count,1)
        self.assertEqual(self.states(),['unconfirmed','cancelled'])

    def test_self_message_event_never_generates_chat_reply(self):
        self.bot._on_message=Mock()
        self.bot._on_event(self.receipt())
        self.bot._on_message.assert_not_called()

    def test_restart_preserves_unknown_and_cancels_unsent_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.db'
            store=Store(path)
            ids=store.delivery_plan(999,'private_101',[[cq_text('FIRST')],[cq_text('SECOND')]],0)
            store.delivery_state(ids[0],'sending')
            store.close()
            store=Store(path)
            try:
                self.assertEqual(store.delivery_status(ids[0]),'unconfirmed')
                self.assertEqual(store.delivery_status(ids[1]),'cancelled')
                self.assertIn('未确认',store.get_short_term()[0]['content'])
            finally:store.close()

    def test_full_reply_timeout_does_not_grow_personality(self):
        self.bot.brain.chat.return_value='FIRST\n\nSECOND'
        self.bot._get_image_bytes=Mock(return_value=(None,None))
        self.bot._get_avatar_desc=Mock(return_value='')
        self.bot.send_msg=Mock(side_effect=SendUnconfirmedError('unknown'))
        event=dict(message_type='private',user_id=101,message_id=1,sender={'nickname':'test'},message=[cq_text('QUESTION')])
        with patch('qq_bot.SEND_LATE_GRACE',0),patch('qq_bot.weather.get_weather',return_value=''),self.assertRaises(SendUnconfirmedError):
            self.bot._on_message(event)
        self.assertEqual([r['role'] for r in self.store.get_short_term()],['user','assistant'])
        self.assertEqual(self.store.get_memories(kind='experience'),[])


class MediaTests(unittest.TestCase):
    def test_original_hash_cache_survives_rkey_and_get_image_failure(self):
        raw=b'GIF89a'+b'image bytes'
        digest=hashlib.md5(raw).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/f'root/.config/QQ/nt_qq_test/nt_data/Pic/2026-10/Ori/{digest}.gif'
            image.parent.mkdir(parents=True);image.write_bytes(raw)
            with patch('qq_media.ROOTFS',Path(tmp)):
                store=Mock();bot=QQBot('ws://unused',Mock(),store)
                bot.get_image=Mock(side_effect=AssertionError('must use cache'))
                self.assertEqual(bot._get_image_bytes([{'type':'image','data':{'file':digest+'.image','url':'https://unused'}}]),(raw,'image/gif'))

    def test_bad_api_url_does_not_skip_valid_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'opt/QQ/resources/app/app_launcher/napcat/cache/test.png'
            image.parent.mkdir(parents=True);image.write_bytes(b'\x89PNG\r\n\x1a\nimage')
            bot=QQBot('ws://unused',Mock(),Mock())
            bot.get_image=Mock(return_value={'retcode':0,'data':{'url':'https://broken','file':'/opt/QQ/resources/app/app_launcher/napcat/cache/test.png'}})
            with patch('qq_media.ROOTFS',Path(tmp)),patch('qq_media.download',side_effect=OSError('failed')):
                self.assertEqual(bot._get_image_bytes([{'type':'image','data':{'file':'test.image','url':'https://broken'}}])[1],'image/png')

    def test_cache_traversal_wrong_hash_and_html_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret=Path(tmp)/'private.txt';secret.write_bytes(b'GIF89asecret')
            with self.assertRaises(FileNotFoundError):qq_media.local_image(str(secret),tmp)
            with self.assertRaises(FileNotFoundError):qq_media.cached_image('../../private.txt',tmp)
            with self.assertRaises(ValueError):qq_media.validated(b'<html>error</html>')
            image=Path(tmp)/('root/.config/QQ/nt_qq_t/nt_data/Pic/2026-10/Ori/'+'0'*32+'.gif')
            image.parent.mkdir(parents=True);image.write_bytes(b'GIF89anot-the-hash')
            with self.assertRaises(FileNotFoundError):qq_media.cached_image('0'*32+'.image',tmp)


class ConfigTests(unittest.TestCase):
    def test_config_migration_preserves_credentials_and_unrelated_connections(self):
        template=json.loads((Path(__file__).parents[2]/'deploy/onebot11.json').read_text())
        old={'network':{'websocketServers':[{'port':8081,'host':'127.0.0.1','token':'secret'}, {'port':8888,'token':'other'}]},
             'imageDownloadProxy':'preserved','timeout':{'baseTimeout':10000,'maxTimeout':1800000}}
        new=delivery_config(old,template)
        self.assertEqual(new['network']['websocketServers'][0]['token'],'secret')
        self.assertTrue(new['network']['websocketServers'][0]['reportSelfMessage'])
        self.assertEqual(new['network']['websocketServers'][1],old['network']['websocketServers'][1])
        self.assertEqual(new['imageDownloadProxy'],'preserved')
        self.assertEqual(new['timeout']['baseTimeout'],30000)
        self.assertEqual(delivery_config(new,template),new)
        self.assertFalse('reportSelfMessage' in old['network']['websocketServers'][0])


if __name__=='__main__':unittest.main()
