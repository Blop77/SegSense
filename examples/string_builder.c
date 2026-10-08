/* A growable string buffer. Appending keeps a pointer into the old block across a realloc. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct sb {
    char *data;
    size_t len;
    size_t cap;
};

static void sb_init(struct sb *b) {
    b->cap = 8;
    b->len = 0;
    b->data = malloc(b->cap);
    b->data[0] = '\0';
}

static void sb_append(struct sb *b, const char *s) {
    size_t n = strlen(s);
    char *end = b->data + b->len;          /* where the new text goes */
    if (b->len + n + 1 > b->cap) {
        while (b->len + n + 1 > b->cap)
            b->cap *= 2;
        b->data = realloc(b->data, b->cap);
    }
    memcpy(end, s, n + 1);
    b->len += n;
}

int main(void) {
    struct sb b;
    sb_init(&b);
    const char *words[] = {"Nemotron ", "patches ", "C ", "memory ", "bugs ", "on ", "Nebius"};
    for (size_t i = 0; i < sizeof words / sizeof *words; i++)
        sb_append(&b, words[i]);
    printf("%s (%zu chars)\n", b.data, b.len);
    free(b.data);
    return 0;
}
