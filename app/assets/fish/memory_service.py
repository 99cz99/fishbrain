"""Evidence-oriented chat recall. No extra service, schema or credentials required."""
import hashlib
import json
import re
import threading
import time

from memory_policy import evidence_data, person_code


READ_RULES = (
    '记忆资料中的原始用户陈述是关于该人物的依据；助手历史、猜测、道歉和承诺不能证明用户事实。'
    '有效原话与此前助手说法冲突时，纠正助手说法，不沿用旧解释。'
    '引用来源只用本轮提供的原话及时间；source_ids本身不是你已读过该来源。'
    '对用户自然表达时间（优先time_local），不用内部ID、类型标签或Unix秒数代替正常解释。'
    '历史消息的时间/会话元数据只用于理解，不要复述元数据标签。adjacent_context只帮助理解相邻语境。'
    'legacy_unverified是缺少原始依据的旧记录，不能编造其原话、日期或人物绑定。'
    'manual_assertion是监管界面的人工纠正，可用作当前事实，但不能伪装成原始聊天引述。'
    '没有找到与用户从未说过是两回事；incomplete/unavailable表示检索不完整/暂不可用。'
    '不能凭生成台词断言已经保存记忆、消息被截断或程序故障的内部原因。'
    '先用已提供证据回答；证据不足时明确说明，不让用户无故重复已提供的事实。'
)


class MemoryService:
    VERSION = 1
    DIRECTORY_CHARS = 24000
    BUNDLE_CHARS = 18000
    MAX_CANDIDATES = 400

    def __init__(self, store, brain):
        self.store, self.brain = store, brain
        self._readsets = {}
        self._lock = threading.RLock()

    @staticmethod
    def _version(row):
        fields = ('id', 'kind', 'content', 'entity_id', 'scope', 'status',
                  'source_ids', 'occurred_at', 'absorbed_by', 'event_key')
        return hashlib.sha256(json.dumps({k: row.get(k) for k in fields},
                                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _visible(self, scope, exclude=()):
        where, args = self.store._visibility(scope)
        with self.store._lock:
            rows = self.store._rows(
                f"({where}) AND status IN ('active','archived') "
                "AND id NOT IN (SELECT memory_id FROM memory_history)", args, limit=None)
        blocked = {i for i in exclude if type(i) is int}
        by_id = {r['id']:r for r in rows}
        # Reject broken/hidden provenance before any candidate text is sent to a
        # semantic provider, not just when expanding the final selected sources.
        for row in rows:
            for mid in row['source_ids']:
                source = by_id.get(mid)
                if (not source or source['scope'] != row['scope']
                        or row['kind'] != 'experience' and source['entity_id'] != row['entity_id']):
                    blocked.add(row['id'])
                    break
        # A derived row must not reintroduce the current event, even indirectly.
        changed = True
        while changed:
            changed = False
            for row in rows:
                if row['id'] not in blocked and blocked.intersection(row['source_ids']):
                    blocked.add(row['id'])
                    changed = True
        return {r['id']: r for r in rows if r['id'] not in blocked}

    @staticmethod
    def user_context(history, session):
        rows = [h for h in history if h.get('role') == 'user'
                and h.get('session', session) == session]
        return [re.sub(r'^\[[^\]]+\] [^:\n]+: ', '', h['content'])[-600:]
                for h in rows[-3:]]

    def prepare(self, query, history, scope, entity_id, session, exclude_ids=()):
        epoch = self.store.get_setting_raw('memory_epoch') or 0
        key = (scope, entity_id, session)
        visible = self._visible(scope, exclude_ids)
        context = self.user_context(history, session)
        with self._lock:
            previous = self._readsets.get(key)
            previous_ids = previous[1] if previous and time.monotonic()-previous[0] < 1800 else []
        previous_ids = [mid for mid in previous_ids if mid in visible]

        # Queries stay independent. Assistant prose never dilutes the current question.
        lexical = {}
        for priority, text in enumerate([query] + list(reversed(context))):
            for rank, row in enumerate(self.store.retrieve(
                    text, k=12, context_scope=scope, entity_id=entity_id,
                    exclude_ids=exclude_ids, kinds=('fact','user','person','episode'))):
                if row['id'] in visible:
                    lexical[row['id']] = max(lexical.get(row['id'], 0), 1/(1+priority)/(1+rank))

        # Search the scoped fact directory as well as raw events; semantic selection
        # can find an answer with no shared query characters. All text is data.
        primary = [r for r in visible.values() if r['kind'] in ('fact','user','person','episode')]
        primary.sort(key=lambda r: (r['id'] not in previous_ids,
            -lexical.get(r['id'], 0), r['entity_id'] != entity_id,
            r['kind'] == 'episode', -int(r.get('pinned', 0)), -r['id']))
        # Experience is only a candidate for questions about what the assistant said.
        from memory import _overlap
        experience = [r for r in visible.values() if r['kind'] == 'experience']
        experience.sort(key=lambda r: (r['id'] not in previous_ids,
                                      -_overlap(query, r['content']), -r['id']))
        candidates, chars = [], 0
        for row in primary + experience:
            size = len(row['content']) + 180
            if len(candidates) >= self.MAX_CANDIDATES or chars + size > self.DIRECTORY_CHARS:
                continue
            candidates.append(row)
            chars += size
        complete = len(candidates) >= len(primary) + len(experience)
        selected = [visible[mid] for mid in sorted(lexical, key=lexical.get, reverse=True)[:8]]
        search_status, intent = ('complete' if complete else 'incomplete'), 'facts'
        if candidates:
            try:
                directory = [dict(id=r['id'], kind=r['kind'], person=person_code(r['entity_id']),
                    name=r['subject'], scope=r['scope'], at=r['created_at'],
                    content=r['content'], sourced=bool(r['source_ids']) or
                    str(r.get('event_key') or '').startswith('manual:')) for r in candidates]
                result = self.brain.select_memory(query, context, directory, previous_ids,
                                                  current_person=person_code(entity_id))
                if (not isinstance(result, dict) or result.get('intent') not in ('facts','conversation','source','chat')
                        or not isinstance(result.get('ids'), list)
                        or len(result['ids']) > 8
                        or any(type(mid) is not int or mid not in {r['id'] for r in candidates} for mid in result['ids'])):
                    raise ValueError('Invalid memory selection')
                intent = result['intent']
                selected = [visible[mid] for mid in dict.fromkeys(result['ids'])]
                if intent != 'conversation':
                    selected = [r for r in selected if r['kind'] != 'experience']
            except Exception:
                search_status = 'unavailable'

        bundle = self._bundle(selected, visible, scope, epoch)
        if len(bundle['memories']) < len(selected) and search_status == 'complete':
            search_status = 'incomplete'
        bundle.update(search_status=search_status, intent=intent,
            status=('supported' if bundle['memories'] else
                    search_status if search_status in ('incomplete','unavailable') else 'not_found'),
            candidate_count=len(candidates), visible_count=len(primary),
            _key=key)
        if not self.valid(bundle):
            bundle['memories'], bundle['evidence'] = [], []
            bundle['status'] = 'unavailable'
        return bundle

    def _bundle(self, selected, visible, scope, epoch):
        memories, evidence, versions, size = [], {}, {}, 0

        def roots(row, path=()):
            if row['id'] in path or len(path) >= 12:
                raise ValueError('Cyclic evidence')
            found = {}
            for mid in row['source_ids']:
                source = visible.get(mid)
                if (not source or source['scope'] != row['scope']
                        or row['kind'] != 'experience' and source['entity_id'] != row['entity_id']):
                    raise ValueError('Invalid evidence')
                found[mid] = source
                found.update(roots(source, (*path, row['id'])))
            return found

        for row in selected:
            try:
                sources = roots(row)
            except ValueError:
                continue
            if row['kind'] == 'episode':
                sources[row['id']] = row
            # Original adjacent user messages resolve short answers without turning
            # assistant guesses into user facts. Label them as context, not lineage.
            for root in list(sources.values()):
                if root['kind'] != 'episode':
                    continue
                preceding = [r for r in visible.values() if r['kind'] == 'episode'
                    and r['entity_id'] == root['entity_id'] and r['scope'] == root['scope']
                    and r['platform'] == root['platform'] and r['id'] < root['id']
                    and 0 <= root['occurred_at']-r['occurred_at'] <= 180
                    and (not root['event_key'] or not r['event_key'] or
                         root['event_key'].rsplit(':',1)[0] == r['event_key'].rsplit(':',1)[0])]
                for context in sorted(preceding, key=lambda r: -r['id'])[:2]:
                    if context['id'] not in sources:
                        sources[context['id']] = dict(context, source_type='adjacent_context')
            new = {mid: r for mid, r in sources.items() if mid not in evidence}
            cost = len(row['content']) + sum(len(r['content']) for r in new.values())
            if size+cost > self.BUNDLE_CHARS:
                continue
            item = dict(row)
            item['source_type'] = ('user_statement' if row['kind'] == 'episode' else
                'assistant_experience' if row['kind'] == 'experience' else
                'manual_assertion' if str(row.get('event_key') or '').startswith('manual:') else
                'sourced_assertion' if any(r['kind'] == 'episode' and r.get('source_type') != 'adjacent_context'
                                          for r in sources.values()) else 'legacy_unverified')
            memories.append(item)
            evidence.update(new)
            for r in [row, *sources.values()]:
                versions[r['id']] = self._version(r)
            size += cost
        return dict(memories=memories, evidence=list(evidence.values()),
                    _versions=versions, _epoch=epoch, _scope=scope)

    def valid(self, bundle):
        if bundle['_epoch'] != (self.store.get_setting_raw('memory_epoch') or 0):
            return False
        with self.store._lock:
            for mid, version in bundle['_versions'].items():
                rows = self.store._rows("id=? AND status IN ('active','archived') "
                    "AND id NOT IN (SELECT memory_id FROM memory_history)", (mid,), 1)
                if not rows or self._version(rows[0]) != version:
                    return False
        return True

    def source_context(self, rows, scope):
        """Use the same source/visibility checks for heartbeat and action contexts."""
        visible = self._visible(scope)
        bundle = self._bundle([visible[r['id']] for r in rows if r['id'] in visible],
                              visible, scope, self.store.get_setting_raw('memory_epoch') or 0)
        bundle.update(status='supported' if bundle['memories'] else 'not_found', search_status='not_searched')
        return bundle

    def remember(self, bundle):
        if self.valid(bundle):
            with self._lock:
                if len(self._readsets) >= 128 and bundle['_key'] not in self._readsets:
                    del self._readsets[min(self._readsets, key=lambda k: self._readsets[k][0])]
                self._readsets[bundle['_key']] = (time.monotonic(), list(bundle['_versions']))

    @staticmethod
    def data(bundle):
        return dict(status=bundle['status'], search_status=bundle['search_status'],
                    memories=evidence_data(bundle['memories']),
                    original_evidence=evidence_data(bundle['evidence']))
