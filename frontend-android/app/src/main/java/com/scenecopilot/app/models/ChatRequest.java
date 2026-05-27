package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class ChatRequest {
    @SerializedName("message")
    public final String message;

    @SerializedName("session_id")
    public final String sessionId;

    public ChatRequest(String message, String sessionId) {
        this.message = message;
        this.sessionId = sessionId;
    }
}
