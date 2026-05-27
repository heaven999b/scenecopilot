package com.scenecopilot.app.models;

import com.google.gson.annotations.SerializedName;

public class DocumentItem {
    @SerializedName("id")
    public String id;

    @SerializedName("title")
    public String title;

    @SerializedName("summary")
    public String summary;

    @SerializedName("snippet")
    public String snippet;

    @SerializedName("score")
    public double score;

    @SerializedName("source_path")
    public String sourcePath;

    @SerializedName("source")
    public String source;
}
