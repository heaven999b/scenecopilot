package com.scenecopilot.app.network;

import androidx.annotation.NonNull;

import com.google.gson.Gson;
import com.scenecopilot.app.models.ReasoningEvent;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.sse.EventSource;
import okhttp3.sse.EventSourceListener;
import okhttp3.sse.EventSources;

public class EventStreamManager {
    public interface Listener {
        void onOpen();
        void onEvent(ReasoningEvent event);
        void onFailure(String message);
    }

    private final OkHttpClient client;
    private final Gson gson = new Gson();
    private final String baseUrl;
    private EventSource currentSource;

    public EventStreamManager(OkHttpClient client, String baseUrl) {
        this.client = client;
        this.baseUrl = baseUrl;
    }

    public void subscribe(String sessionId, String runId, Listener listener) {
        close();

        String url = baseUrl + "api/events/" + sessionId;
        if (runId != null && !runId.isEmpty()) {
            url += "?run_id=" + runId;
        }
        Request request = new Request.Builder().url(url).build();
        currentSource = EventSources.createFactory(client).newEventSource(request, new EventSourceListener() {
            @Override
            public void onOpen(@NonNull EventSource eventSource, @NonNull Response response) {
                listener.onOpen();
            }

            @Override
            public void onEvent(@NonNull EventSource eventSource, String id, String type, @NonNull String data) {
                ReasoningEvent event = gson.fromJson(data, ReasoningEvent.class);
                if (event.eventType == null || event.eventType.isEmpty()) {
                    event.eventType = type;
                }
                listener.onEvent(event);
            }

            @Override
            public void onFailure(@NonNull EventSource eventSource, Throwable t, Response response) {
                String message = t != null ? t.getMessage() : "Unknown SSE error";
                listener.onFailure(message);
            }
        });
    }

    public void close() {
        if (currentSource != null) {
            currentSource.cancel();
            currentSource = null;
        }
    }
}
