package com.homesh.tv;

import android.util.Base64;
import android.util.Log;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Every request of an app whose two copies are the same, sent to whichever
 * machine answers -- under one address, so the page never reloads to change.
 *
 * <p>Moving the page between machines (AppActivity's other mode) has to load
 * it again, because a page belongs to the address it came from and asks only
 * that one. For an app that is the same on both -- SpaXepedition: the same data,
 * kept in step through GitHub, no sign-in of its own -- there is a better way.
 * The page stays at the PC's address and this answers each of its requests,
 * from the PC when it answers in time and from the standby when it does not.
 * The reader sees nothing but the page; what changed is where the bytes came
 * from. The PC declares an app the same (PHONE_APPS, server/app/main.py),
 * because only its owner can know that.
 *
 * <p>Reads arrive through shouldInterceptRequest. Writes cannot -- Android
 * gives that callback no body -- so the page's fetch is wrapped (SHIM) to hand
 * a write's body to {@link #send}, which answers through the same choice.
 *
 * <p>Which machine: the PC, until a request to it fails or is slow to start
 * answering; from then on straight to the standby, at no cost per request, until
 * the keepalive has heard the PC answer twice running (Follow). A failure is
 * believed at once because somebody is waiting on that request; a recovery
 * waits to be sure, so that a weak signal cannot make it trade back and forth.
 *
 * <p>A write is sent again to the standby only when it cannot have reached the
 * PC: a connection never made, or a PC that said it is unavailable. One the PC
 * may have taken and not yet answered is reported as failed, not repeated --
 * two copies of a comment are worse than a retry the reader chooses.
 */
final class Relay {

    interface Listener {
        /** The machine answering has changed; true for the standby. */
        void answering(boolean standby);
    }

    private static final String TAG = "HomeshRelay";
    /** To connect and to hear the start of an answer: past it, the PC is too busy to wait for. */
    private static final int PC_CONNECT_MS = 3000;
    private static final int PC_FIRST_BYTE_MS = 5000;
    private static final int STANDBY_CONNECT_MS = 8000;
    private static final int STANDBY_FIRST_BYTE_MS = 15000;
    /** Once an answer has started, a slow signal is a slow download, not a dead machine. */
    private static final int BODY_MS = 30000;
    /** A write may take longer than a read to answer: saving is work. */
    private static final int WRITE_ANSWER_MS = 20000;

    /**
     * Put into the page's HTML before anything else runs: a fetch that writes
     * goes to the phone instead, which sends it to whichever machine answers.
     * Reads, and writes with a body that is not text, go as before.
     */
    static final String SHIM = "<script>(function(){if(window.__homeshRelay||!window.HomeshRelay)return;"
            + "window.__homeshRelay=1;var n=0,wait={},f=window.fetch;"
            + "window.__relayDone=function(id,r){var w=wait[id];delete wait[id];if(!w)return;"
            + "if(r.error){w.no(new TypeError('Failed to fetch'));return;}"
            + "var s=atob(r.body||''),b=new Uint8Array(s.length);for(var i=0;i<s.length;i++)b[i]=s.charCodeAt(i);"
            + "w.ok(new Response(r.status===204||r.status===205?null:b,{status:r.status,statusText:r.statusText||'',headers:r.headers||{}}));};"
            + "window.fetch=function(input,init){try{var m=((init&&init.method)||(input&&input.method)||'GET').toUpperCase();"
            + "var body=init&&init.body;"
            + "if(m!=='GET'&&m!=='HEAD'&&(body==null||typeof body==='string')){"
            + "var u=new URL(typeof input==='string'?input:input.url,location.href);"
            + "if(u.origin===location.origin){var h={};new Headers((init&&init.headers)||{}).forEach(function(v,k){h[k]=v;});"
            + "return new Promise(function(ok,no){var id=++n;wait[id]={ok:ok,no:no};"
            + "HomeshRelay.send(id,m,u.href,JSON.stringify(h),body==null?'':body);});}}}catch(e){}"
            + "return f.apply(this,arguments);};})();</script>";

    private final String pc;
    private final String standby;
    private final Listener listener;
    /** The PC has failed a request and not yet been heard back: the standby goes first. */
    private volatile boolean away;

    Relay(String pc, String standby, Listener listener) {
        this.pc = pc;
        this.standby = standby;
        this.listener = listener;
    }

    boolean away() {
        return away;
    }

    /** Where requests go first; set by the keepalive, and by a request that failed. */
    void setAway(boolean now) {
        if (away == now) return;
        away = now;
        Log.i(TAG, now ? "answering from the standby" : "answering from the PC");
        listener.answering(now);
    }

    /** True for an address this relay answers for: the PC's, where the page lives. */
    boolean covers(String url) {
        return url != null && (url.equals(pc) || url.startsWith(pc + "/") || url.startsWith(pc + "?"));
    }

    /**
     * A read, from whichever machine answers; null leaves it to the WebView
     * (another site, a method this does not carry, or nothing answering at all,
     * which the WebView then reports the way it reports any failed page).
     */
    WebResourceResponse fetch(WebResourceRequest request, boolean mainFrame) {
        String url = request.getUrl().toString();
        String method = request.getMethod();
        if (!covers(url) || !("GET".equals(method) || "HEAD".equals(method))) return null;
        String path = url.substring(pc.length());
        Answer a = null;
        if (!away) {
            a = usable(open(pc, path, method, request.getRequestHeaders(), null, true));
            if (a == null) setAway(true);
        }
        if (a == null) a = usable(open(standby, path, method, request.getRequestHeaders(), null, true));
        if (a == null && away) {
            // The standby is not answering either; the PC may be after all.
            a = usable(open(pc, path, method, request.getRequestHeaders(), null, true));
            if (a != null) setAway(false);
        }
        if (a == null) return null;
        try {
            return a.response(mainFrame);
        } catch (IOException e) {
            a.close();
            return null;
        }
    }

    /** A read's answer worth passing on: one that began, from a machine that could answer. */
    private static Answer usable(Answer a) {
        if (a != null && (a.lost || a.unavailable())) {
            a.close();
            return null;
        }
        return a;
    }

    /**
     * A write handed over by SHIM, sent to whichever machine may take it; the
     * answer is the JSON __relayDone takes. Called on a thread of its own.
     */
    String send(String method, String url, String headersJson, String body) {
        JSONObject out = new JSONObject();
        try {
            if (!covers(url)) throw new IOException("not this app's address");
            String path = url.substring(pc.length());
            Map<String, String> headers = new HashMap<>();
            JSONObject h = new JSONObject(headersJson == null || headersJson.isEmpty() ? "{}" : headersJson);
            for (Iterator<String> k = h.keys(); k.hasNext(); ) {
                String name = k.next();
                headers.put(name, h.optString(name));
            }
            byte[] bytes = body == null ? new byte[0] : body.getBytes(StandardCharsets.UTF_8);
            Answer a = null;
            if (!away) {
                a = open(pc, path, method, headers, bytes, false);
                if (a == null || a.unavailable()) {
                    // Connection never made, or the PC said it cannot: not taken
                    // there, so safe to send to the standby.
                    if (a != null) a.close();
                    a = null;
                    setAway(true);
                } else if (a.lost) {
                    a.close();
                    throw new IOException("the PC took the request and did not answer");
                }
            }
            if (a == null) a = open(standby, path, method, headers, bytes, false);
            if (a == null || a.lost) throw new IOException("neither machine answered");
            out.put("status", a.status);
            out.put("statusText", a.reason);
            JSONObject rh = new JSONObject();
            for (Map.Entry<String, String> e : a.headers.entrySet()) rh.put(e.getKey(), e.getValue());
            out.put("headers", rh);
            out.put("body", Base64.encodeToString(a.readAll(), Base64.NO_WRAP));
        } catch (Exception e) {
            Log.i(TAG, "write failed: " + e.getMessage());
            try {
                out = new JSONObject().put("error", true);
            } catch (Exception ignored) {
                // (a constant key: cannot fail)
            }
        }
        return out.toString();
    }

    /**
     * One request to one machine. Null when no connection was made in time;
     * an Answer with {@code lost} set when it was made, the request sent, and
     * no answer began in time -- the one case a write must not be repeated.
     */
    private Answer open(String base, String path, String method, Map<String, String> headers,
                        byte[] body, boolean read) {
        boolean toPc = base.equals(pc);
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + path).openConnection();
            c.setConnectTimeout(toPc ? PC_CONNECT_MS : STANDBY_CONNECT_MS);
            c.setReadTimeout(read ? (toPc ? PC_FIRST_BYTE_MS : STANDBY_FIRST_BYTE_MS) : WRITE_ANSWER_MS);
            c.setRequestMethod(method);
            c.setInstanceFollowRedirects(true);
            c.setUseCaches(true);
            for (Map.Entry<String, String> h : headers.entrySet()) {
                String name = h.getKey().toLowerCase(Locale.ROOT);
                // Left to the connection: it decompresses what it asked to be
                // compressed, and its cache answers the conditional requests.
                if (name.equals("accept-encoding") || name.equals("host") || name.equals("cookie")
                        || name.equals("content-length") || name.startsWith("if-")) continue;
                c.setRequestProperty(h.getKey(), h.getValue());
            }
            String cookies = CookieManager.getInstance().getCookie(base + path);
            if (cookies != null && !cookies.isEmpty()) c.setRequestProperty("Cookie", cookies);
            if (body != null) {
                c.setDoOutput(true);
                c.setFixedLengthStreamingMode(body.length);
            }
            c.connect();
        } catch (IOException e) {
            if (c != null) c.disconnect();
            return null;
        }
        try {
            if (body != null) {
                try (OutputStream os = c.getOutputStream()) {
                    os.write(body);
                }
            }
            int status = c.getResponseCode();
            c.setReadTimeout(BODY_MS);
            Answer a = new Answer(c, status);
            List<String> set = c.getHeaderFields().get("Set-Cookie");
            if (set != null) {
                for (String v : set) CookieManager.getInstance().setCookie(base + path, v);
            }
            return a;
        } catch (IOException e) {
            Answer a = new Answer(c, 0);
            a.lost = true;
            return a;
        }
    }

    /** An answer that has begun: its status and headers, its body still to read. */
    private static final class Answer {
        final HttpURLConnection c;
        final int status;
        final String reason;
        final Map<String, String> headers = new HashMap<>();
        boolean lost;

        Answer(HttpURLConnection c, int status) {
            this.c = c;
            this.status = status;
            String r = null;
            try {
                r = c.getResponseMessage();
            } catch (IOException ignored) {
                // (no message: one is made up below)
            }
            this.reason = r == null || r.trim().isEmpty() ? "OK" : r.trim();
            for (Map.Entry<String, List<String>> e : c.getHeaderFields().entrySet()) {
                String name = e.getKey();
                if (name == null || e.getValue() == null || e.getValue().isEmpty()) continue;
                String lower = name.toLowerCase(Locale.ROOT);
                // Already undone by the connection, or the WebView's to work out.
                if (lower.equals("content-encoding") || lower.equals("content-length")
                        || lower.equals("transfer-encoding") || lower.equals("set-cookie")
                        || lower.equals("connection")) continue;
                headers.put(name, e.getValue().get(e.getValue().size() - 1));
            }
        }

        /** The machine said it cannot answer now: busy, starting, or behind a proxy that lost it. */
        boolean unavailable() {
            return !lost && (status == 502 || status == 503 || status == 504);
        }

        WebResourceResponse response(boolean mainFrame) throws IOException {
            if (status < 100 || (status >= 300 && status < 400) || status > 599) {
                throw new IOException("not an answer a WebView takes: " + status);
            }
            String type = c.getContentType();
            String mime = "application/octet-stream";
            String charset = null;
            if (type != null) {
                String[] parts = type.split(";");
                mime = parts[0].trim();
                for (int i = 1; i < parts.length; i++) {
                    String p = parts[i].trim();
                    if (p.toLowerCase(Locale.ROOT).startsWith("charset=")) charset = p.substring(8).replace("\"", "");
                }
            }
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            if (in == null) in = new java.io.ByteArrayInputStream(new byte[0]);
            in = new Closing(in, c);
            if (mainFrame && mime.equals("text/html")) {
                // The page's own HTML: the write shim goes in before its
                // scripts can run, so their first save already uses it.
                in = new java.io.ByteArrayInputStream(withShim(readStream(in), charset));
                charset = "utf-8";
            }
            return new WebResourceResponse(mime, charset, status, reason, headers, in);
        }

        byte[] readAll() throws IOException {
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            if (in == null) return new byte[0];
            try {
                return readStream(in);
            } finally {
                c.disconnect();
            }
        }

        void close() {
            c.disconnect();
        }
    }

    private static byte[] readStream(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        try {
            for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
        } finally {
            in.close();
        }
        return out.toByteArray();
    }

    private static byte[] withShim(byte[] html, String charset) {
        String text = new String(html, charset == null ? StandardCharsets.UTF_8
                : java.nio.charset.Charset.forName(charset));
        String lower = text.toLowerCase(Locale.ROOT);
        int head = lower.indexOf("<head");
        int at = head < 0 ? 0 : lower.indexOf('>', head) + 1;
        return (text.substring(0, at) + SHIM + text.substring(at)).getBytes(StandardCharsets.UTF_8);
    }

    /** The connection let go once the WebView has read the body. */
    private static final class Closing extends FilterInputStream {
        private final HttpURLConnection c;

        Closing(InputStream in, HttpURLConnection c) {
            super(in);
            this.c = c;
        }

        @Override
        public void close() throws IOException {
            try {
                super.close();
            } finally {
                c.disconnect();
            }
        }
    }
}
