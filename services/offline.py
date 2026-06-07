"""
Offline Mode Service — Cache claim data locally for field work without internet.

Strategy:
  - Claims data is stored in localStorage on the browser
  - Photos are stored as base64 in IndexedDB
  - When online, sync everything back to the server
  - Uses Service Worker for background sync
"""
import json
import datetime


def get_offline_cache_routes():
    """
    Return JavaScript code for offline caching.
    This is embedded in templates via Jinja.
    """
    return '''
// FloodClaims Pro — Offline Cache Manager
const CACHE_NAME = 'floodclaims-pro-v1';
const OFFLINE_DB_NAME = 'floodclaims-offline';
const OFFLINE_DB_VERSION = 1;

// ── IndexedDB for photos ──────────────────────────────────────────────────────
function openOfflineDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(OFFLINE_DB_NAME, OFFLINE_DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains('photos')) {
        db.createObjectStore('photos', { keyPath: 'id', autoIncrement: true });
      }
      if (!db.objectStoreNames.contains('claims')) {
        db.createObjectStore('claims', { keyPath: 'claim_id' });
      }
      if (!db.objectStoreNames.contains('sync_queue')) {
        db.createObjectStore('sync_queue', { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

// ── Save claim data for offline use ───────────────────────────────────────────
async function saveClaimOffline(claimId, claimData) {
  try {
    const db = await openOfflineDB();
    const tx = db.transaction('claims', 'readwrite');
    tx.objectStore('claims').put({
      claim_id: claimId,
      data: claimData,
      saved_at: new Date().toISOString(),
    });
    // Also save to localStorage as backup
    localStorage.setItem('fc_claim_' + claimId, JSON.stringify(claimData));
    localStorage.setItem('fc_claim_' + claimId + '_time', new Date().toISOString());
    return true;
  } catch (e) {
    console.error('Offline save failed:', e);
    return false;
  }
}

// ── Get cached claim data ─────────────────────────────────────────────────────
async function getCachedClaim(claimId) {
  try {
    const db = await openOfflineDB();
    const tx = db.transaction('claims', 'readonly');
    const req = tx.objectStore('claims').get(claimId);
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result ? req.result.data : null);
      req.onerror = () => reject(req.error);
    });
  } catch (e) {
    // Fallback to localStorage
    const data = localStorage.getItem('fc_claim_' + claimId);
    return data ? JSON.parse(data) : null;
  }
}

// ── Save photo for later sync ─────────────────────────────────────────────────
async function savePhotoOffline(claimId, file, caption) {
  try {
    const db = await openOfflineDB();
    const reader = new FileReader();
    return new Promise((resolve, reject) => {
      reader.onload = async () => {
        const tx = db.transaction('photos', 'readwrite');
        const photoData = {
          claim_id: claimId,
          filename: file.name,
          data: reader.result,  // base64
          caption: caption || '',
          saved_at: new Date().toISOString(),
          synced: false,
        };
        tx.objectStore('photos').add(photoData);
        // Add to sync queue
        const qTx = db.transaction('sync_queue', 'readwrite');
        qTx.objectStore('sync_queue').add({
          type: 'photo_upload',
          claim_id: claimId,
          data: photoData,
          created_at: new Date().toISOString(),
        });
        resolve(true);
      };
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  } catch (e) {
    console.error('Photo offline save failed:', e);
    return false;
  }
}

// ── Sync when back online ─────────────────────────────────────────────────────
async function syncOfflineData() {
  if (!navigator.onLine) return { synced: 0, offline: true };

  try {
    const db = await openOfflineDB();
    const tx = db.transaction('sync_queue', 'readonly');
    const queue = await new Promise((resolve, reject) => {
      const req = tx.objectStore('sync_queue').getAll();
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });

    let synced = 0;
    for (const item of queue) {
      try {
        if (item.type === 'photo_upload') {
          const formData = new FormData();
          // Convert base64 back to blob
          const byteStr = atob(item.data.data.split(',')[1]);
          const mime = item.data.data.split(',')[0].split(':')[1].split(';')[0];
          const ab = new ArrayBuffer(byteStr.length);
          const ia = new Uint8Array(ab);
          for (let i = 0; i < byteStr.length; i++) ia[i] = byteStr.charCodeAt(i);
          const blob = new Blob([ab], { type: mime });
          formData.append('photos', blob, item.data.filename);
          formData.append('caption', item.data.caption);

          const resp = await fetch('/portal/' + item.claim_id + '/upload', {
            method: 'POST',
            body: formData,
          });
          if (resp.ok) synced++;
        }
      } catch (e) {
        console.error('Sync item failed:', e);
      }
    }

    // Clear synced items
    const clearTx = db.transaction('sync_queue', 'readwrite');
    clearTx.objectStore('sync_queue').clear();

    return { synced, offline: false };
  } catch (e) {
    console.error('Sync failed:', e);
    return { synced: 0, error: e.message };
  }
}

// ── Auto-sync when connection returns ─────────────────────────────────────────
window.addEventListener('online', () => {
  console.log('FloodClaims Pro: Back online, syncing...');
  syncOfflineData().then(result => {
    if (result.synced > 0) {
      alert('FloodClaims Pro: ' + result.synced + ' item(s) synced successfully!');
    }
  });
});

// ── Check online status ───────────────────────────────────────────────────────
function updateOnlineStatus() {
  const indicator = document.getElementById('online-status');
  if (indicator) {
    if (navigator.onLine) {
      indicator.textContent = '🟢 Online';
      indicator.style.color = '#10b981';
    } else {
      indicator.textContent = '🔴 Offline — Data saved locally';
      indicator.style.color = '#ef4444';
    }
  }
}

document.addEventListener('DOMContentLoaded', updateOnlineStatus);
window.addEventListener('online', updateOnlineStatus);
window.addEventListener('offline', updateOnlineStatus);
'''


def get_offline_page_data(claim_id, claim, room_data):
    """
    Prepare claim data for offline caching.
    Returns JSON-serializable dict for embedding in page.
    """
    return {
        "claim_id": claim_id,
        "claim_number": claim.get("claim_number", ""),
        "client_name": claim.get("client_name", ""),
        "property_address": claim.get("property_address", ""),
        "status": claim.get("status", "New"),
        "total_estimate": claim.get("total_estimate", 0),
        "flood_date": claim.get("flood_date", ""),
        "water_category": claim.get("water_category", ""),
        "water_class": claim.get("water_class", ""),
        "rooms": [
            {
                "name": rd["room"]["name"],
                "subtotal": rd["room"].get("subtotal", 0),
                "items": [
                    {
                        "description": i.get("description", ""),
                        "quantity": i.get("quantity", 0),
                        "unit": i.get("unit", "ea"),
                        "unit_cost": i.get("unit_cost", 0),
                        "total": i.get("total", 0),
                    }
                    for i in rd["line_items"]
                ],
                "photos": [
                    {
                        "filename": p.get("filename", ""),
                        "caption": p.get("caption", ""),
                        "ai_description": p.get("ai_description", ""),
                    }
                    for p in rd.get("room_photos", [])
                ],
            }
            for rd in room_data
        ],
        "cached_at": datetime.datetime.now().isoformat(),
    }
