package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

import java.util.Map;

public class ClientIncidentRequest {
    @SerializedName("session_id")
    public final String sessionId;

    @SerializedName("incident_type")
    public final String incidentType;

    @SerializedName("run_id")
    public final String runId;

    @SerializedName("message")
    public final String message;

    @SerializedName("details")
    public final Map<String, Object> details;

    public ClientIncidentRequest(String sessionId, String incidentType, String runId, String message, Map<String, Object> details) {
        this.sessionId = sessionId;
        this.incidentType = incidentType;
        this.runId = runId;
        this.message = message;
        this.details = details;
    }
}
