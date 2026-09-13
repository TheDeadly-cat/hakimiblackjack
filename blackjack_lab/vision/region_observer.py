"""Bounded source-region observation, independent of model predictions.

Full native RGB hashes decide exact equality. Small grayscale previews describe
change, while native grayscale edges describe relative detail. Detail scores are
warnings, never calibrated readability probabilities or a new rank threshold.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
import hashlib
import threading
import time

from .deps import ImageRejected, load_cv2, load_numpy

OBSERVATION_POLICY = 'native-roi-detail-and-exact-change-1'
SCHEDULING_POLICY = 'roi-exact-completed-inference-1'


def inference_fingerprints(record):
    # Current adapters intentionally retain detections outside configured ROIs
    # as unassigned review candidates, so their full image context must count.
    return {'regions': {r['region_id']: r['content_digest'] for r in record['regions']},
            'detector_context_digest': record['detector_context_digest']}


def same_as_completed_inference(record, completed):
    """Never compare with the previous merely observed source frame."""
    return bool(completed) and inference_fingerprints(record) == completed


class RegionObserver:
    def __init__(self, style, *, evidence_limit=1200, expected_size=None):
        if not 1 <= len(style.regions) <= 16 or evidence_limit < 1:
            raise ImageRejected('实时区域观察需 1–16 个标定区域及有界记录容量')
        self.style = style
        self.expected_size = tuple(expected_size) if expected_size else None
        self._lock = threading.Lock()
        self._token = None
        self._last_ns = None
        self._previous = {}
        self._peaks = {}
        self._cache = OrderedDict()
        self._records = deque(maxlen=evidence_limit)
        self._latest = None
        self.samples = self.evictions = self.stale_samples = 0

    def warmup(self):
        # Run once on the model worker before source playback/capture starts.
        cv2, np = load_cv2(), load_numpy()
        cv2.Laplacian(np.zeros((3, 3), dtype=np.uint8), cv2.CV_16S)

    def observe(self, packet, current_token, *, origin='source'):
        if packet is None or packet.token() != current_token():
            return None
        token = packet.token()
        key = (packet.source_id, packet.stream_epoch, packet.layout_version, packet.frame_id)
        start = time.perf_counter_ns()
        with self._lock:
            if packet.token() != current_token():
                self.stale_samples += 1
                return None
            if key in self._cache:
                return deepcopy(self._cache[key])
            changed_token = token != self._token
            previous = {} if changed_token else self._previous
            ordered = changed_token or self._last_ns is None or packet.observed_monotonic_ns >= self._last_ns
            cv2, np = load_cv2(), load_numpy()
            regions, next_previous, next_peaks = [], {}, {}
            context_digest = None
            for name, region in sorted(self.style.regions.items()):
                box = region.to_pixels(packet.width, packet.height)
                x, y, w, h = (box[k] for k in ('x', 'y', 'w', 'h'))
                if w <= 0 or h <= 0:
                    raise ImageRejected('区域观察遇到空裁区，请重新标定')
                pixels = np.ascontiguousarray(packet.pixels[y:y+h, x:x+w, :3])
                digest = hashlib.blake2b(pixels, digest_size=16).hexdigest()
                if x==0 and y==0 and w==packet.width and h==packet.height:
                    context_digest=digest
                gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
                low, high, _, _ = cv2.minMaxLoc(gray)
                detail = float(cv2.mean(cv2.absdiff(cv2.Laplacian(gray, cv2.CV_16S), 0))[0])
                thumb = cv2.resize(gray, (min(96, w), min(64, h)), interpolation=cv2.INTER_AREA)
                prior = previous.get(name) if ordered else None
                same = prior is not None and prior['digest'] == digest
                change = (float(np.mean(np.abs(thumb.astype(np.int16)-prior['thumb']))) / 255
                          if prior is not None and thumb.shape == prior['thumb'].shape else None)
                history = (deque(maxlen=256) if changed_token or not ordered else
                           deque(self._peaks.get(name, ()), maxlen=256))
                while history and packet.observed_monotonic_ns-history[0][0] > 5_000_000_000:
                    history.popleft()
                peak = max((v for _, v in history), default=detail)
                # A relative warning only. Neither threshold changes model calls,
                # rank acceptance, support votes, or the existing expiry policy.
                state = ('little_detail' if high-low < 8 else
                         'detail_decreased' if peak > 0 and detail < peak*.5 else 'observed')
                regions.append({'region_id': name, 'bbox': box, 'content_digest': digest,
                    'same_as_previous_source': same, 'change_magnitude': change,
                    'native_gray_range': high-low, 'native_edge_energy': detail,
                    'detail_relative_to_recent_peak': detail/peak if peak > 0 else None,
                    'detail_state': state})
                history.append((packet.observed_monotonic_ns, detail))
                next_previous[name] = {'digest': digest, 'thumb': thumb}
                next_peaks[name] = history
            if context_digest is None:
                context_digest=hashlib.blake2b(np.ascontiguousarray(packet.pixels),digest_size=16).hexdigest()
            record = {'source_token': token.as_dict(), 'frame_id': packet.frame_id,
                'media_time_ns': packet.media_time_ns, 'observed_monotonic_ns': packet.observed_monotonic_ns,
                'image_size': [packet.width, packet.height], 'regions': regions, 'origin': origin,
                'detector_context_digest': context_digest, 'inference_scope': 'full_captured_image',
                'ordered_source_observation': ordered,
                'calibrated_size_matches': ([packet.width, packet.height] == list(self.expected_size)
                                             if self.expected_size else None),
                'compute_ms': (time.perf_counter_ns()-start)/1e6}
            if packet.token() != current_token():
                self.stale_samples += 1
                return None
            if changed_token:
                self._cache.clear()
            if ordered:
                self._token, self._last_ns = token, packet.observed_monotonic_ns
                self._previous, self._peaks, self._latest = next_previous, next_peaks, record
            self.samples += 1
            if len(self._records) == self._records.maxlen:
                self.evictions += 1
            self._records.append(record)
            self._cache[key] = record
            while len(self._cache) > 64:
                self._cache.popitem(last=False)
            return deepcopy(record)

    def latest(self, current_token):
        with self._lock:
            if self._latest is None or self._token != current_token():
                return None
            return deepcopy(self._latest)

    def snapshot(self):
        with self._lock:
            return {'policy': OBSERVATION_POLICY, 'samples': self.samples, 'evictions': self.evictions,
                    'stale_samples_discarded': self.stale_samples, 'records': deepcopy(list(self._records)),
                    'readability_probability': None,
                    'scope': 'Relative native detail and exact region pixels; no rank or identity inference.'}
