package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class ClientIncidentResponse {
    @SerializedName("accepted")
    public boolean accepted;

    @SerializedName("incident_type")
    public String incidentType;

    @SerializedName("session_id")
    public String sessionId;

    @SerializedName("run_id")
    public String runId;
}
