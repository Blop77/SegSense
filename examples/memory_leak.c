/* Builds a word list and never frees it. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

char **split(const char *line, int *count) {
    char *copy = strdup(line);
    char **words = malloc(16 * sizeof *words);
    *count = 0;
    for (char *tok = strtok(copy, " "); tok && *count < 16; tok = strtok(NULL, " "))
        words[(*count)++] = strdup(tok);
    return words;
}

static void print_words(const char *line) {
    int n;
    char **words = split(line, &n);
    for (int i = 0; i < n; i++)
        printf("%d: %s\n", i, words[i]);
}

int main(void) {
    print_words("ride the bike then play football");
    return 0;
}
