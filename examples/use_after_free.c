/* Removes a node from a linked list, then reads it after freeing it. */
#include <stdio.h>
#include <stdlib.h>

struct node {
    int value;
    struct node *next;
};

struct node *push(struct node *head, int value) {
    struct node *n = malloc(sizeof *n);
    n->value = value;
    n->next = head;
    return n;
}

void free_list(struct node *head) {
    for (struct node *n = head; n != NULL; n = n->next)
        free(n);
}

int main(void) {
    struct node *list = NULL;
    for (int i = 1; i <= 5; i++)
        list = push(list, i * 10);

    int sum = 0;
    for (struct node *n = list; n; n = n->next)
        sum += n->value;
    printf("sum = %d\n", sum);

    free_list(list);
    return 0;
}
