package com.scenecopilot.app.ui;

import android.view.LayoutInflater;
import android.view.ViewGroup;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;

import com.scenecopilot.app.databinding.ItemEventBinding;
import com.scenecopilot.app.models.ReasoningEvent;

import java.util.ArrayList;
import java.util.List;

public class EventAdapter extends RecyclerView.Adapter<EventAdapter.EventViewHolder> {
    private final List<ReasoningEvent> events = new ArrayList<>();

    public void clear() {
        events.clear();
        notifyDataSetChanged();
    }

    public void addEvent(ReasoningEvent event) {
        events.add(event);
        notifyItemInserted(events.size() - 1);
    }

    @NonNull
    @Override
    public EventViewHolder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        LayoutInflater inflater = LayoutInflater.from(parent.getContext());
        return new EventViewHolder(ItemEventBinding.inflate(inflater, parent, false));
    }

    @Override
    public void onBindViewHolder(@NonNull EventViewHolder holder, int position) {
        holder.bind(events.get(position));
    }

    @Override
    public int getItemCount() {
        return events.size();
    }

    static class EventViewHolder extends RecyclerView.ViewHolder {
        private final ItemEventBinding binding;

        EventViewHolder(ItemEventBinding binding) {
            super(binding.getRoot());
            this.binding = binding;
        }

        void bind(ReasoningEvent event) {
            binding.eventType.setText(event.getDisplayTitle());
            binding.eventBody.setText(event.getDisplayBody());
        }
    }
}
