package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

import java.util.ArrayList;
import java.util.List;

public class DocumentSearchResponse {
    @SerializedName("query")
    public String query;

    @SerializedName("items")
    public List<DocumentItem> items = new ArrayList<>();
}
