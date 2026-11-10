/* One read-only query on feature-page entry. No timer or idle worker. */
static int nx_animation_query(const char *helper) {
    int pipefd[2];if(pipe(pipefd))return -1;
    pid_t pid=fork();
    if(pid<0){close(pipefd[0]);close(pipefd[1]);return -1;}
    if(!pid){
        close(pipefd[0]);if(dup2(pipefd[1],STDOUT_FILENO)<0)_exit(69);
        close(pipefd[1]);execl(helper,helper,"status",(char *)0);_exit(69);
    }
    close(pipefd[1]);char text[80];unsigned used=0;ssize_t n;
    while(used<sizeof text-1){n=read(pipefd[0],text+used,sizeof text-1-used);if(n>0)used+=(unsigned)n;else if(n<0&&errno==EINTR)continue;else break;}
    close(pipefd[0]);text[used]=0;int status;pid_t got;
    do {got=waitpid(pid,&status,0);}while(got<0&&errno==EINTR);
    unsigned camera,mode,installed;char extra;
    if(got!=pid||!WIFEXITED(status)||WEXITSTATUS(status)||sscanf(text,"%u %u %u %c",&camera,&mode,&installed,&extra)!=3||camera<2||mode>2||installed>1)return -1;
    return (int)mode;
}
