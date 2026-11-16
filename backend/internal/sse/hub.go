package sse

import (
	"sync"

	"polyglot/backend/internal/store"
)

type Hub struct {
	mu          sync.RWMutex
	subscribers map[string]map[chan store.TaskEvent]struct{}
}

func NewHub() *Hub {
	return &Hub{
		subscribers: make(map[string]map[chan store.TaskEvent]struct{}),
	}
}

func (h *Hub) Subscribe(taskID string) (chan store.TaskEvent, func()) {
	ch := make(chan store.TaskEvent, 128)
	h.mu.Lock()
	if _, ok := h.subscribers[taskID]; !ok {
		h.subscribers[taskID] = make(map[chan store.TaskEvent]struct{})
	}
	h.subscribers[taskID][ch] = struct{}{}
	h.mu.Unlock()

	unsubscribe := func() {
		h.mu.Lock()
		if subs, ok := h.subscribers[taskID]; ok {
			if _, exists := subs[ch]; exists {
				delete(subs, ch)
				close(ch)
			}
			if len(subs) == 0 {
				delete(h.subscribers, taskID)
			}
		}
		h.mu.Unlock()
	}
	return ch, unsubscribe
}

func (h *Hub) Publish(taskID string, event store.TaskEvent) {
	h.mu.RLock()
	defer h.mu.RUnlock()

	subs, ok := h.subscribers[taskID]
	if !ok {
		return
	}
	for ch := range subs {
		select {
		case ch <- event:
		default:
			// Drop if subscriber buffer is full so execution never blocks
		}
	}
}
